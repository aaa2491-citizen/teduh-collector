"""Shared settings. Values come from environment variables or a .env file."""
import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# MONGO_URI = shared Atlas M0 (Jakarta forecasts, villages, sweeps, historical sources)
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
# LOCAL_MONGO_URI = optional local MongoDB used only for the >=1 GB / index demo
LOCAL_MONGO_URI = os.getenv("LOCAL_MONGO_URI") or None
# National (non-Jakarta) responses: "files" = gzip JSONL on disk (fits free Atlas),
# "mongo" = store in MONGO_URI (only if that is a local/big MongoDB)
NATIONAL_SINK = os.getenv("NATIONAL_SINK", "mongo")
NATIONAL_DIR = os.getenv("NATIONAL_DIR", "raw_national")
DB_NAME = os.getenv("DB_NAME", "teduh_raw")

BMKG_URL = "https://api.bmkg.go.id/publik/prakiraan-cuaca"
# BMKG allows 60 requests/min per IP. 1.05 s spacing = ~57/min, leaves a safety margin.
SECONDS_PER_REQUEST = float(os.getenv("SECONDS_PER_REQUEST", "1.05"))
JAKARTA_PREFIX = "31."                             # DKI Jakarta province code
JAKARTA_EVERY_HOURS = float(os.getenv("JAKARTA_EVERY_HOURS", "3"))
NATIONAL_RETENTION_DAYS = int(os.getenv("NATIONAL_RETENTION_DAYS", "14"))
USER_AGENT = f"TEDUH-APAN5400-student-project ({os.getenv('CONTACT', 'no-contact-set')})"

# Fallback point if village coordinates are not loaded yet (central Jakarta)
JAKARTA_CENTER = ("Jakarta (center)", -6.2088, 106.8456)

# Storage cap of your Atlas tier, for the % warning in 04_checks.py (M0=512, Flex=5000)
STORAGE_LIMIT_MB = float(os.getenv("STORAGE_LIMIT_MB", "5000"))
