"""STEP 7: health checks, course-concept demos, and the handoff export.

  python 04_checks.py                       # sizes, completeness, release times, errors
  python 04_checks.py --unwind 31.71.01.1001  # $unwind demo: one release -> one row per slot
  python 04_checks.py --explain 31.71.01.1001 # index vs collection scan (COLLSCAN vs IXSCAN)
  python 04_checks.py --export jakarta_slots.csv   # flat Jakarta slots for Keshia + Danaparamita
"""
import argparse

import config
from datetime import datetime, timedelta, timezone

from common import get_db

UTC = timezone.utc

# BMKG nests slots as data[] -> cuaca[day] -> [slot], so we unwind three times.
UNWIND_SLOTS = [
    {"$unwind": "$payload.data"},
    {"$unwind": "$payload.data.cuaca"},     # one doc per day (each is still a list)
    {"$unwind": "$payload.data.cuaca"},     # one doc per 3-hour slot
    {"$project": {
        "_id": 0, "adm4": 1, "analysis_date": 1,
        "village": "$payload.lokasi.desa", "kecamatan": "$payload.lokasi.kecamatan",
        "local_datetime": "$payload.data.cuaca.local_datetime",
        "utc_datetime": "$payload.data.cuaca.utc_datetime",
        "weather_desc": "$payload.data.cuaca.weather_desc",
        "weather_desc_en": "$payload.data.cuaca.weather_desc_en",
        "temp_c": "$payload.data.cuaca.t", "humidity_pct": "$payload.data.cuaca.hu",
        "cloud_pct": "$payload.data.cuaca.tcc", "wind_kmh": "$payload.data.cuaca.ws",
        "wind_dir": "$payload.data.cuaca.wd", "visibility": "$payload.data.cuaca.vs_text",
        # tp (rain mm) is not in BMKG's documented field list; kept in case it exists
        "rain_mm": "$payload.data.cuaca.tp"}},
]


def gb(n):
    return f"{n / 1e9:.3f} GB"


def summary(db):
    print("=== Collection sizes (data + indexes is what counts toward your Atlas storage cap) ===")
    total = 0
    for name in sorted(db.list_collection_names()):
        s = db.command("collStats", name)
        total += s["size"] + s.get("totalIndexSize", 0)
        print(f"{name:18} docs={s['count']:>10,}  data={gb(s['size'])}  "
              f"onDisk={gb(s.get('storageSize', 0))}  indexes={gb(s.get('totalIndexSize', 0))}")
    pct = total / (config.STORAGE_LIMIT_MB * 1e6) * 100
    print(f"TOTAL {total / 1e6:.0f} MB = {pct:.0f}% of the {config.STORAGE_LIMIT_MB:.0f} MB cap"
          + ("   <-- WARNING: past 80%, act now" if pct > 80 else ""))

    print("\n=== Last 5 Jakarta passes (expected = active Jakarta villages) ===")
    for s in db.sweeps.find({"scope": "jakarta"}).sort("started_at", -1).limit(5):
        print(s["_id"], "expected", s["expected"], s["counts"])

    nat = db.sweeps.find_one({"scope": "national"}, sort=[("started_at", -1)])
    if nat:
        hrs = (datetime.now(UTC) - nat["started_at"].replace(tzinfo=UTC)).total_seconds() / 3600
        print(f"\n=== National sweep {nat['_id']}: {nat['done']:,}/{nat['expected']:,} "
              f"after {hrs:.1f} h (finished: {nat.get('finished_at')})")

    print("\n=== Forecast releases seen for Jakarta, last 3 days (shows BMKG's real release times) ===")
    since = datetime.now(UTC) - timedelta(days=3)
    for r in db.bmkg_forecasts.aggregate([
        {"$match": {"adm4": {"$regex": r"^31\."}, "first_retrieved_at": {"$gte": since}}},
        {"$group": {"_id": "$analysis_date", "villages": {"$sum": 1},
                    "first_seen": {"$min": "$first_retrieved_at"}}},
        {"$sort": {"_id": 1}}]):
        print(f"issued {r['_id']}  villages={r['villages']}  first seen {r['first_seen']}")

    print("\n=== Fetch outcomes, last 24 h ===")
    for r in db.fetch_log.aggregate([
        {"$match": {"ts": {"$gte": datetime.now(UTC) - timedelta(hours=24)}}},
        {"$group": {"_id": {"scope": "$scope", "result": "$result"}, "n": {"$sum": 1},
                    "avg_ms": {"$avg": "$ms"}}},
        {"$sort": {"_id.scope": 1, "n": -1}}]):
        print(r["_id"], r["n"], f"avg {r['avg_ms']:.0f} ms")
    print("\nInactive villages (BMKG 404):", db.villages.count_documents({"active": False}))


def unwind_demo(db, adm4):
    rows = list(db.bmkg_forecasts.aggregate(
        [{"$match": {"adm4": adm4}}, {"$sort": {"analysis_date": -1}}, {"$limit": 1}] + UNWIND_SLOTS))
    print(f"{len(rows)} slots from the latest release of {adm4}")
    for r in rows:
        print(r)


def stages(plan):
    found = []
    if isinstance(plan, dict):
        if "stage" in plan:
            found.append(plan["stage"])
        for v in plan.values():
            found += stages(v)
    elif isinstance(plan, list):
        for v in plan:
            found += stages(v)
    return found


def explain_demo(db, adm4):
    flt = {"adm4": adm4, "analysis_date": {"$gte": datetime.now(UTC) - timedelta(days=7)}}
    for label, hint in [("with index", "uniq_village_release"), ("no index", {"$natural": 1})]:
        res = db.command("explain", {"find": "bmkg_forecasts", "filter": flt, "hint": hint},
                         verbosity="executionStats")
        ex = res["executionStats"]
        print(f"{label:11} plan={stages(res['queryPlanner']['winningPlan'])} "
              f"docsExamined={ex['totalDocsExamined']:,} returned={ex['nReturned']} "
              f"time={ex['executionTimeMillis']} ms")


def export(db, path):
    import pandas as pd
    rows = db.bmkg_forecasts.aggregate(
        [{"$match": {"adm4": {"$regex": r"^31\."}}}] + UNWIND_SLOTS, allowDiskUse=True)
    df = pd.DataFrame(list(rows))
    df.to_csv(path, index=False)
    print(f"Wrote {len(df):,} slot rows to {path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--unwind")
    ap.add_argument("--explain")
    ap.add_argument("--export")
    ap.add_argument("--local", action="store_true", help="use LOCAL_MONGO_URI instead of Atlas")
    a = ap.parse_args()
    db = get_db(config.LOCAL_MONGO_URI if a.local else None)
    if a.unwind:
        unwind_demo(db, a.unwind)
    elif a.explain:
        explain_demo(db, a.explain)
    elif a.export:
        export(db, a.export)
    else:
        summary(db)
