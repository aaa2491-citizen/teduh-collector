"""STEP 1: call BMKG once and print the real JSON structure.

Usage:  python 00_smoke_test.py               (default: 31.71.01.1001, BMKG's own example code)
        python 00_smoke_test.py 31.71.06.1001
"""
import json
import sys

import requests

import config

adm4 = sys.argv[1] if len(sys.argv) > 1 else "31.71.01.1001"
r = requests.get(config.BMKG_URL, params={"adm4": adm4},
                 headers={"User-Agent": config.USER_AGENT}, timeout=30)
print("URL      :", r.url)
print("Status   :", r.status_code)
print("Bytes    :", len(r.content))
print("Headers  :", {k: v for k, v in r.headers.items()
                     if k.lower().startswith(("x-ratelimit", "retry-after", "content-type"))})
r.raise_for_status()
data = r.json()

out = f"sample_{adm4}.json"
with open(out, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2, ensure_ascii=False)
print(f"Saved full response to {out}\n")


def tree(obj, name="root", depth=0, max_depth=5):
    pad = "  " * depth
    if isinstance(obj, dict):
        print(f"{pad}{name}: dict ({len(obj)} keys)")
        if depth < max_depth:
            for k, v in obj.items():
                tree(v, k, depth + 1)
    elif isinstance(obj, list):
        print(f"{pad}{name}: list (len={len(obj)})  <- showing first item only")
        if obj and depth < max_depth:
            tree(obj[0], f"{name}[0]", depth + 1)
    else:
        print(f"{pad}{name}: {type(obj).__name__} = {str(obj)[:60]}")


tree(data)

# Quick facts the collector relies on
print("\n--- checks ---")
print("lokasi:", data.get("lokasi"))
slots, analysis = 0, set()
for block in data.get("data", []):
    days = block.get("cuaca", [])
    print("days in cuaca:", len(days), "| slots per day:", [len(d) for d in days])
    for day in days:
        for s in day:
            slots += 1
            analysis.add(s.get("analysis_date"))
print("total slots:", slots)
print("analysis_date values (forecast issue time):", analysis)
