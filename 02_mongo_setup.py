"""STEP 4: create collections and indexes. Safe to re-run."""
from pymongo import ASCENDING, DESCENDING

import config
from common import get_db


def setup(db, label):
    f = db.bmkg_forecasts
    # One document per (village, forecast release). Unique = repeated pulls of the
    # same release become no-ops (the dedup rule on the Risks slide).
    # Prefix rule (Module 7): this index also serves queries on adm4 alone.
    f.create_index([("adm4", ASCENDING), ("analysis_date", ASCENDING)],
                   unique=True, name="uniq_village_release")
    f.create_index([("first_retrieved_at", DESCENDING)], name="by_retrieved")
    # TTL: only docs that HAVE expire_at are deleted (national ones). Jakarta has
    # no expire_at, so it is kept for the whole project.
    f.create_index("expire_at", expireAfterSeconds=0, name="ttl_national")

    db.fetch_log.create_index([("sweep_id", ASCENDING), ("adm4", ASCENDING)])
    db.fetch_log.create_index("ts", expireAfterSeconds=30 * 24 * 3600, name="ttl_30d")
    db.villages.create_index([("is_jakarta", ASCENDING), ("active", ASCENDING)])
    for c in ("raw_nasa_power", "raw_open_meteo", "raw_ridership"):
        db[c].create_index("record_key", unique=True)
    print(f"[{label}] indexes ready:", sorted(f.index_information()))


setup(get_db(), "atlas/primary")
if config.LOCAL_MONGO_URI:
    setup(get_db(config.LOCAL_MONGO_URI), "local")
