"""STEP 8 (later, for the demo): load the national gzip files into a LOCAL MongoDB.

This gives you the >=1 GB dataset and a big collection for the index vs no-index
comparison, without touching the free Atlas cluster.

  python 05_load_national_files.py                 # all files in raw_national/
  python 05_load_national_files.py --days 3        # only the 3 most recent files
Then:  python 04_checks.py --local --explain 32.73.01.1001
"""
import argparse
import glob
import gzip
import json
import os
from datetime import datetime

from pymongo import UpdateOne
from pymongo.errors import BulkWriteError

import config
from common import get_db


def analysis_date(payload):
    for block in (payload or {}).get("data", []):
        for day in block.get("cuaca", []):
            for slot in day:
                raw = slot.get("analysis_date")
                if raw:
                    return datetime.fromisoformat(raw.replace("Z", "+00:00")), raw
    return None, None


def read_lines(path):
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            for line in f:
                yield line
    except (EOFError, OSError):
        print(f"  {os.path.basename(path)}: truncated at the end (collector was stopped), "
              "kept everything before that")


ap = argparse.ArgumentParser()
ap.add_argument("--days", type=int)
a = ap.parse_args()
if not config.LOCAL_MONGO_URI:
    raise SystemExit("Set LOCAL_MONGO_URI in .env first")

db = get_db(config.LOCAL_MONGO_URI)
coll = db.bmkg_forecasts
files = sorted(glob.glob(os.path.join(config.NATIONAL_DIR, "bmkg_*.jsonl.gz")))
if a.days:
    files = files[-a.days:]

for path in files:
    ops, n, skipped = [], 0, 0
    for line in read_lines(path):
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            skipped += 1
            continue
        ad, raw = analysis_date(rec.get("payload"))
        if rec.get("status") != "ok" or ad is None:
            skipped += 1
            continue
        retrieved = datetime.fromisoformat(rec["retrieved_at"])
        doc = {"adm4": rec["adm4"], "analysis_date": ad, "analysis_date_raw": raw,
               "scope": "national", "first_retrieved_at": retrieved,
               "first_sweep_id": rec["sweep_id"], "source_url": rec["source_url"],
               "source": "BMKG", "payload": rec["payload"]}
        ops.append(UpdateOne({"adm4": rec["adm4"], "analysis_date": ad},
                             {"$setOnInsert": doc, "$max": {"last_seen_at": retrieved}},
                             upsert=True))
        n += 1
        if len(ops) == 1000:
            try:
                coll.bulk_write(ops, ordered=False)
            except BulkWriteError as e:
                print("  bulk write warning:", e.details.get("writeErrors", [])[:1])
            ops = []
    if ops:
        coll.bulk_write(ops, ordered=False)
    print(f"{os.path.basename(path)}: {n:,} responses loaded, {skipped:,} skipped (errors/404)")

s = db.command("collStats", "bmkg_forecasts")
print(f"Local bmkg_forecasts: {s['count']:,} docs, {s['size'] / 1e9:.2f} GB data")
