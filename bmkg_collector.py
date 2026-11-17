"""STEP 5: the long-running BMKG collector.

One process, one rate limiter, two jobs:
  * Jakarta pass  : all active 31.* villages, every JAKARTA_EVERY_HOURS (priority) -> Atlas
  * National sweep: every active village, rolling + resumable -> gzip files (NATIONAL_SINK)

Daily-job usage (no laptop running 24/7):
  python bmkg_collector.py --jakarta-once                         # ~5 min
  python bmkg_collector.py --national-only --national-minutes 320 # resumes where it stopped

Other usage:
  python bmkg_collector.py                    # run forever (both jobs)
  python bmkg_collector.py --jakarta-once     # one Jakarta pass, then exit (good first test)
  python bmkg_collector.py --national-limit 200   # test: Jakarta pass + 200 national villages
  python bmkg_collector.py --jakarta-only     # only the 3-hourly Jakarta job
"""
import argparse
import gzip
import json
import logging
import os
import random
import time
from datetime import datetime, timedelta, timezone

import requests

import config
from common import RateLimiter, get_db, make_session

UTC = timezone.utc
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    handlers=[logging.StreamHandler(), logging.FileHandler("collector.log")])
log = logging.getLogger("collector")


def now():
    return datetime.now(UTC)


def parse_analysis_date(payload):
    """Forecast issue time. BMKG puts `analysis_date` on every slot in data[].cuaca[day][slot]."""
    try:
        for block in payload.get("data", []):
            for day in block.get("cuaca", []):
                for slot in day:
                    raw = slot.get("analysis_date")
                    if raw:
                        return datetime.fromisoformat(raw.replace("Z", "+00:00")), raw
    except (AttributeError, TypeError, ValueError):
        pass
    return None, None


class Collector:
    def __init__(self, db):
        self.db = db
        self.limiter = RateLimiter(config.SECONDS_PER_REQUEST)
        self.http = make_session()
        self._fh, self._fh_day, self._since_flush = None, None, 0

    # ---------- national -> gzip JSONL files (keeps free Atlas under 512 MB) ----------
    def to_file(self, record):
        day = record["retrieved_at"][:10]
        if day != self._fh_day:                      # one file per UTC day
            if self._fh:
                self._fh.close()
            os.makedirs(config.NATIONAL_DIR, exist_ok=True)
            self._fh = gzip.open(os.path.join(config.NATIONAL_DIR, f"bmkg_{day}.jsonl.gz"),
                                 "at", encoding="utf-8")
            self._fh_day = day
        self._fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._since_flush += 1
        if self._since_flush >= 200:
            self._fh.flush()
            self._since_flush = 0

    # ---------- HTTP with retry + backoff ----------
    def fetch(self, adm4, max_attempts=5):
        for attempt in range(1, max_attempts + 1):
            self.limiter.wait()
            t0 = time.monotonic()
            try:
                r = self.http.get(config.BMKG_URL, params={"adm4": adm4}, timeout=30)
                ms = int((time.monotonic() - t0) * 1000)
                if r.status_code == 200:
                    return "ok", r.json(), ms, len(r.content), r.url
                if r.status_code == 404:
                    return "not_found", None, ms, 0, r.url
                if r.status_code == 429 or r.status_code >= 500:
                    retry_after = r.headers.get("Retry-After", "")
                    wait = float(retry_after) if retry_after.isdigit() else min(60, 2 ** attempt)
                    log.warning("%s -> HTTP %s, retry %d in %.0fs", adm4, r.status_code, attempt, wait)
                    time.sleep(wait + random.random())
                    continue
                return f"http_{r.status_code}", None, ms, 0, r.url
            except (requests.RequestException, ValueError) as e:
                wait = min(60, 2 ** attempt)
                log.warning("%s -> %s, retry %d in %ds", adm4, type(e).__name__, attempt, wait)
                time.sleep(wait + random.random())
        return "failed", None, 0, 0, None

    # ---------- storage ----------
    def store(self, db, adm4, payload, scope, sweep_id, retrieved_at, url):
        analysis_date, analysis_raw = parse_analysis_date(payload)
        if analysis_date is None:
            db.bmkg_unparsed.insert_one({"adm4": adm4, "retrieved_at": retrieved_at,
                                         "source_url": url, "payload": payload})
            return "unparsed"
        doc = {
            "adm4": adm4,
            "analysis_date": analysis_date,        # forecast issue time (UTC)
            "analysis_date_raw": analysis_raw,     # exactly as BMKG sent it
            "scope": scope,
            "first_retrieved_at": retrieved_at,
            "first_sweep_id": sweep_id,
            "source_url": url,
            "source": "BMKG",                      # attribution is required by BMKG
            "payload": payload,                    # untouched raw response
        }
        if not adm4.startswith(config.JAKARTA_PREFIX):
            doc["expire_at"] = retrieved_at + timedelta(days=config.NATIONAL_RETENTION_DAYS)
        res = db.bmkg_forecasts.update_one(
            {"adm4": adm4, "analysis_date": analysis_date},
            {"$setOnInsert": doc, "$set": {"last_seen_at": retrieved_at}, "$inc": {"times_seen": 1}},
            upsert=True)
        return "new" if res.upserted_id else "dup"

    def process(self, adm4, scope, sweep_id):
        status, payload, ms, nbytes, url = self.fetch(adm4)
        ts = now()
        result = status
        is_jakarta = adm4.startswith(config.JAKARTA_PREFIX)
        if not is_jakarta and config.NATIONAL_SINK == "files":
            # National village: write raw response + log line to disk, nothing to Atlas
            # except progress (sweeps) and the 404 flag (villages).
            self.to_file({"adm4": adm4, "status": status, "ms": ms, "bytes": nbytes,
                          "retrieved_at": ts.isoformat(), "sweep_id": sweep_id,
                          "source_url": url, "source": "BMKG", "payload": payload})
            if status == "not_found":
                self.db.villages.update_one({"_id": adm4}, {"$set": {
                    "active": False, "inactive_reason": "BMKG 404", "inactive_at": ts}})
            return "file_" + status
        if status == "ok":
            result = self.store(self.db, adm4, payload, scope, sweep_id, ts, url)
            loc = payload.get("lokasi") or {}
            if loc:   # fill names/coordinates once, for the locations table downstream
                self.db.villages.update_one(
                    {"_id": adm4, "lat": {"$exists": False}},
                    {"$set": {k: loc.get(k) for k in
                              ("desa", "kecamatan", "kotkab", "provinsi", "lat", "lon", "timezone")}})
        elif status == "not_found":
            self.db.villages.update_one({"_id": adm4},
                                        {"$set": {"active": False, "inactive_reason": "BMKG 404",
                                                  "inactive_at": ts}})
        self.db.fetch_log.insert_one({"ts": ts, "adm4": adm4, "scope": scope, "sweep_id": sweep_id,
                                      "status": status, "result": result, "ms": ms, "bytes": nbytes})
        return result

    # ---------- jobs ----------
    def jakarta_pass(self):
        sweep_id = "jkt-" + now().strftime("%Y%m%dT%H%M")
        codes = [v["_id"] for v in self.db.villages.find(
            {"is_jakarta": True, "active": True}, {"_id": 1}).sort("_id", 1)]
        started, counts, failed = now(), {}, []
        for code in codes:
            r = self.process(code, "jakarta", sweep_id)
            counts[r] = counts.get(r, 0) + 1
            if r in ("failed",) or r.startswith("http_"):
                failed.append(code)
        for code in failed:                      # one more try at the end of the pass
            r = self.process(code, "jakarta", sweep_id + "-retry")
            counts["retried_" + r] = counts.get("retried_" + r, 0) + 1
        self.db.sweeps.insert_one({"_id": sweep_id, "scope": "jakarta", "started_at": started,
                                   "finished_at": now(), "expected": len(codes), "counts": counts})
        log.info("Jakarta pass %s done: %s", sweep_id, counts)

    def current_national_sweep(self):
        sweep = self.db.sweeps.find_one({"scope": "national", "finished_at": None},
                                        sort=[("started_at", -1)])
        if sweep:
            log.info("Resuming national sweep %s after %s", sweep["_id"], sweep.get("last_adm4"))
            return sweep
        sweep = {"_id": "nat-" + now().strftime("%Y%m%dT%H%M"), "scope": "national",
                 "started_at": now(), "finished_at": None, "last_adm4": "", "done": 0,
                 "expected": self.db.villages.count_documents({"active": True})}
        self.db.sweeps.insert_one(sweep)
        log.info("Started national sweep %s (%s villages)", sweep["_id"], sweep["expected"])
        return sweep

    def next_national_code(self, sweep):
        v = self.db.villages.find_one({"active": True, "_id": {"$gt": sweep["last_adm4"]}},
                                      {"_id": 1}, sort=[("_id", 1)])
        return v["_id"] if v else None

    def run(self, jakarta_only=False, jakarta_once=False, national_limit=None,
            national_only=False, national_minutes=None):
        every = timedelta(hours=config.JAKARTA_EVERY_HOURS)
        next_jakarta = datetime.max.replace(tzinfo=UTC) if national_only else now()
        deadline = now() + timedelta(minutes=national_minutes) if national_minutes else None
        sweep = None if jakarta_only else self.current_national_sweep()
        national_done = 0
        while True:
            if now() >= next_jakarta:
                pass_start = now()
                self.jakarta_pass()
                next_jakarta = pass_start + every
                if jakarta_once:
                    return
                continue
            if jakarta_only:
                time.sleep(min(60, max(1, (next_jakarta - now()).total_seconds())))
                continue
            code = self.next_national_code(sweep)
            if code is None:
                self.db.sweeps.update_one({"_id": sweep["_id"]}, {"$set": {"finished_at": now()}})
                log.info("National sweep %s finished", sweep["_id"])
                sweep = self.current_national_sweep()
                continue
            self.process(code, "national", sweep["_id"])
            sweep["last_adm4"] = code
            self.db.sweeps.update_one({"_id": sweep["_id"]},
                                      {"$set": {"last_adm4": code}, "$inc": {"done": 1}})
            national_done += 1
            if national_done % 500 == 0:
                log.info("National %s: at %s (%d this run)", sweep["_id"], code, national_done)
            if national_limit and national_done >= national_limit:
                log.info("Reached --national-limit %d, stopping", national_limit)
                return
            if deadline and now() >= deadline:
                log.info("Time budget used: %d national villages this run, resume next run",
                         national_done)
                return


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--jakarta-only", action="store_true")
    ap.add_argument("--jakarta-once", action="store_true")
    ap.add_argument("--national-limit", type=int)
    ap.add_argument("--national-only", action="store_true")
    ap.add_argument("--national-minutes", type=int, help="stop national sweep after N minutes")
    a = ap.parse_args()
    collector = Collector(get_db())
    try:
        collector.run(a.jakarta_only, a.jakarta_once, a.national_limit,
                      a.national_only, a.national_minutes)
    except KeyboardInterrupt:
        log.info("Stopped by user. Progress is saved; rerun to resume.")
    finally:
        if collector._fh:
            collector._fh.close()
