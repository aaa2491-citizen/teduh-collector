"""STEP 6: load the three historical sources as raw records.

  python 03_load_historical.py nasa
  python 03_load_historical.py openmeteo            # default model ecmwf_ifs025
  python 03_load_historical.py openmeteo --model ecmwf_ifs
  python 03_load_historical.py ridership-api        # Satu Data Jakarta API (preferred)
  python 03_load_historical.py ridership jumlah-penumpang-angkutan-umum-yang-terlayani-perhari.csv

Each record keeps source URL, retrieval time and a record_key (unique index), so
re-running never creates duplicates.
"""
import argparse
import hashlib
import math
from datetime import date, datetime, timedelta, timezone

import requests

import config
from common import get_db, make_session

UTC = timezone.utc
START = date(2024, 1, 1)


def jakarta_points(db):
    """One point per Jakarta kota, averaged from BMKG village coordinates (aggregation, Module 7).
    Falls back to central Jakarta if the collector has not filled coordinates yet."""
    rows = list(db.villages.aggregate([
        {"$match": {"is_jakarta": True, "lat": {"$ne": None}, "lon": {"$ne": None}}},
        {"$group": {"_id": "$kotkab", "lat": {"$avg": {"$toDouble": "$lat"}},
                    "lon": {"$avg": {"$toDouble": "$lon"}}, "villages": {"$sum": 1}}},
        {"$sort": {"_id": 1}},
    ]))
    if not rows:
        print("No village coordinates yet -> using central Jakarta only")
        return [config.JAKARTA_CENTER]
    for r in rows:
        print(f"  point {r['_id']}: {r['lat']:.4f}, {r['lon']:.4f} ({r['villages']} villages)")
    return [(r["_id"], r["lat"], r["lon"]) for r in rows]


def save(db, coll, key, doc):
    doc["record_key"] = key
    db[coll].replace_one({"record_key": key}, doc, upsert=True)


def load_nasa(db, http):
    end = date.today() - timedelta(days=1)
    for name, lat, lon in jakarta_points(db):
        r = http.get("https://power.larc.nasa.gov/api/temporal/daily/point", timeout=180, params={
            "parameters": "PRECTOTCORR,T2M,T2M_MAX,T2M_MIN,RH2M", "community": "AG",
            "latitude": round(lat, 4), "longitude": round(lon, 4),
            "start": START.strftime("%Y%m%d"), "end": end.strftime("%Y%m%d"), "format": "JSON"})
        r.raise_for_status()
        data = r.json()
        rain = data["properties"]["parameter"]["PRECTOTCORR"]
        fill = data.get("header", {}).get("fill_value", -999)
        valid = [d for d, v in rain.items() if v != fill]
        print(f"NASA {name}: {len(rain)} days, {len(rain) - len(valid)} missing (fill={fill}), "
              f"last valid day {max(valid) if valid else None}")
        save(db, "raw_nasa_power", f"nasa|{name}|{START}|{end}", {
            "source": "NASA POWER", "point_name": name, "lat": lat, "lon": lon,
            "start": str(START), "end": str(end), "fill_value": fill,
            "retrieved_at": datetime.now(UTC), "source_url": r.url, "payload": data})


def load_openmeteo(db, http, model):
    """Previous Runs API: `_previous_day1` = the value forecast one day ahead (lead time 24 h)."""
    end = date.today() - timedelta(days=1)
    hourly = ("precipitation,precipitation_previous_day1,"
              "temperature_2m_previous_day1,relative_humidity_2m_previous_day1")
    for name, lat, lon in jakarta_points(db):
        chunk_start = START
        while chunk_start <= end:                       # 6-month chunks keep each doc small
            chunk_end = min(end, date(chunk_start.year + (chunk_start.month + 5) // 12,
                                      (chunk_start.month + 5) % 12 + 1, 1) - timedelta(days=1))
            r = http.get("https://previous-runs-api.open-meteo.com/v1/forecast", timeout=180, params={
                "latitude": round(lat, 4), "longitude": round(lon, 4), "hourly": hourly,
                "models": model, "timezone": "Asia/Jakarta",
                "start_date": str(chunk_start), "end_date": str(chunk_end)})
            r.raise_for_status()
            data = r.json()
            series = data.get("hourly", {}).get("precipitation_previous_day1", [])
            nulls = sum(v is None for v in series)
            flag = "  <-- mostly empty: try another --model" if series and nulls / len(series) > 0.5 else ""
            print(f"Open-Meteo {name} {chunk_start}..{chunk_end}: {len(series)} hours, {nulls} null{flag}")
            save(db, "raw_open_meteo", f"om|{model}|{name}|{chunk_start}|{chunk_end}", {
                "source": "Open-Meteo Previous Runs API", "model": model, "point_name": name,
                "lat": lat, "lon": lon, "start": str(chunk_start), "end": str(chunk_end),
                "retrieved_at": datetime.now(UTC), "source_url": r.url, "payload": data})
            chunk_start = chunk_end + timedelta(days=1)


def quality_report(df, date_col="Date", mode_col="Mode", value_col="Total_passengers"):
    """Known issues in this dataset (documented at naufaldi.dev/projects/jakarta_transport)."""
    import pandas as pd
    d = df.rename(columns={date_col: "Date", mode_col: "Mode", value_col: "Total_passengers"})
    d["Date"] = pd.to_datetime(d["Date"], errors="coerce")
    d["Mode"] = d["Mode"].astype(str).str.strip().str.upper()   # API mixes 'krl' and 'KRL'
    d["Total_passengers"] = pd.to_numeric(
        d["Total_passengers"].astype(str).str.replace(",", "", regex=False), errors="coerce")
    print("Non-numeric passenger values:", d["Total_passengers"].isna().sum())
    print("\n=== Ridership quality report ===")
    print("Unparseable dates:", d["Date"].isna().sum())
    print("Duplicate (Date, Mode) rows:", d.duplicated(["Date", "Mode"]).sum())
    for mode, g in d.groupby("Mode"):
        full = pd.date_range(g["Date"].min(), g["Date"].max(), freq="D")
        missing = len(full.difference(g["Date"]))
        zeros = (g["Total_passengers"] == 0).sum()
        print(f"{mode:22} {g['Date'].min().date()}..{g['Date'].max().date()}  rows={len(g):4}  "
              f"missing_days={missing:3}  zero_days={zeros:3}")
    print("Check these known issues:")
    print(" - MIKROTRANS split out from 2026-01-01 (TRANSJAKARTA drops ~500k)")
    print(" - train zeros on 2024-12-16/17, half volume on 2024-12-31")
    print(" - KRL & KCI COMMUTER BANDARA roughly doubled 2025-12-13..31")
    print(" - real dips: Eid, 2024-11-27 election, late Aug 2025 unrest (not errors)\n")


SATUDATA_API = ("https://ws.jakarta.go.id/gateway/DataPortalSatuDataJakarta/1.0/satudata"
                "?kategori=dataset&tipe=detail&url=jumlah-penumpang-angkutan-umum-yang-terlayani-perhari")


def load_ridership_api(db, http):
    import pandas as pd
    r = http.get(SATUDATA_API, timeout=180)
    r.raise_for_status()
    body = r.json()
    rows = body.get("data", [])
    print(f"API message={body.get('message')} total_file={body.get('total_file')} rows={len(rows)}")
    sha = hashlib.sha256(r.content).hexdigest()
    # One raw document per pull (~0.8 MB, well under MongoDB's 16 MB document limit)
    save(db, "raw_ridership", f"satudata-api|{sha}", {
        "source": "Satu Data Jakarta API", "retrieved_at": datetime.now(UTC),
        "source_url": SATUDATA_API, "total_file": body.get("total_file"),
        "content_sha256": sha, "payload": body})
    df = pd.DataFrame(rows)
    print("Columns:", list(df.columns))
    quality_report(df, "tanggal", "jenis_moda", "jumlah_penumpang_per_hari")


def load_ridership(db, path):
    import pandas as pd
    sha = hashlib.sha256(open(path, "rb").read()).hexdigest()
    if path.lower().endswith((".xls", ".xlsx")):
        df = pd.read_excel(path)
    else:
        df = pd.read_csv(path, sep=None, engine="python")   # sniff , or ;
    print("Shape:", df.shape)
    print("Columns:", list(df.columns))
    print(df.head(10).to_string())
    for col in df.columns:
        low = str(col).lower()
        if any(k in low for k in ("tanggal", "date", "periode", "bulan", "tahun")):
            print(f"  {col}: min={df[col].min()}  max={df[col].max()}  unique={df[col].nunique()}")
        if any(k in low for k in ("jenis", "moda", "mode", "angkutan")):
            print(f"  {col} value counts:\n{df[col].value_counts().to_string()}")
    if {"Date", "Mode", "Total_passengers"} <= set(df.columns):
        quality_report(df)
    loaded_at = datetime.now(UTC)
    docs = []
    for i, row in enumerate(df.to_dict("records")):
        clean = {str(k): (None if isinstance(v, float) and math.isnan(v) else v) for k, v in row.items()}
        docs.append({"record_key": f"{sha}|{i}", "source": "Satu Data Jakarta", "source_file": path,
                     "file_sha256": sha, "row_num": i, "loaded_at": loaded_at, "row": clean})
    for i in range(0, len(docs), 5000):
        for d in docs[i:i + 5000]:
            db.raw_ridership.replace_one({"record_key": d["record_key"]}, d, upsert=True)
    print(f"Stored {len(docs)} rows in raw_ridership (file sha256 {sha[:12]}...)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("source", choices=["nasa", "openmeteo", "ridership", "ridership-api"])
    ap.add_argument("file", nargs="?")
    ap.add_argument("--model", default="ecmwf_ifs025")
    a = ap.parse_args()
    db, http = get_db(), make_session()
    if a.source == "nasa":
        load_nasa(db, http)
    elif a.source == "openmeteo":
        load_openmeteo(db, http, a.model)
    elif a.source == "ridership-api":
        load_ridership_api(db, http)
    else:
        if not a.file:
            ap.error("ridership needs the downloaded CSV/XLS path")
        load_ridership(db, a.file)
