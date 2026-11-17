"""STEP 3: load all desa/kelurahan codes (adm4) into Mongo `villages`.

Source: BMKG uses codes from Kepmendagri No. 100.1.1-6117 Tahun 2022, so use
        cahyadsn/wilayah -> db/archive/wilayah_2023.sql (NOT the 2025 db/wilayah.sql).
Usage:  python 01_build_village_list.py wilayah_2023.sql
"""
import csv
import re
import sys
from datetime import datetime, timezone

from pymongo import UpdateOne

import config
from common import get_db

CODE_RE = re.compile(r"^\d{2}\.\d{2}\.\d{2}\.\d{4}$")
SQL_ROW_RE = re.compile(r"\('(\d{2}\.\d{2}\.\d{2}\.\d{4})',\s*'((?:[^'\\]|\\.|'')*)'")


def from_sql(path):
    text = open(path, encoding="utf-8", errors="replace").read()
    return {code: name.replace("\\'", "'").replace("''", "'") for code, name in SQL_ROW_RE.findall(text)}


def from_csv(path):
    rows = list(csv.reader(open(path, encoding="utf-8", errors="replace")))
    header, body = rows[0], rows[1:]
    code_col = next(i for i in range(len(header))
                    if sum(bool(CODE_RE.match(r[i].strip())) for r in body[:2000] if len(r) > i) > 100)
    name_col = code_col + 1 if code_col + 1 < len(header) else code_col
    return {r[code_col].strip(): r[name_col].strip() for r in body
            if len(r) > code_col and CODE_RE.match(r[code_col].strip())}


path = sys.argv[1]
villages = from_sql(path) if path.endswith(".sql") else from_csv(path)
jakarta = [c for c in villages if c.startswith(config.JAKARTA_PREFIX)]
print(f"Villages found: {len(villages):,}   (deck says 83,762, which is the 2025 count; 2022 list may differ)")
print(f"Jakarta (31.*): {len(jakarta):,}    (proposal says 267)")

db = get_db()
now = datetime.now(timezone.utc)
ops = [UpdateOne({"_id": code},
                 {"$set": {"name": name, "prov": code[:2],
                           "is_jakarta": code.startswith(config.JAKARTA_PREFIX)},
                  "$setOnInsert": {"active": True, "added_at": now}},
                 upsert=True)
       for code, name in villages.items()]
for i in range(0, len(ops), 5000):
    db.villages.bulk_write(ops[i:i + 5000], ordered=False)
print("Upserted into", config.DB_NAME + ".villages:", db.villages.count_documents({}))
