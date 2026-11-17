import time

import requests
from pymongo import MongoClient

import config


def get_db(uri=None):
    import certifi
    uri = uri or config.MONGO_URI
    kwargs = {"serverSelectionTimeoutMS": 10000}
    if uri.startswith("mongodb+srv"):          # Atlas pakai TLS
        kwargs["tlsCAFile"] = certifi.where()  # pakai daftar sertifikat dari certifi
    client = MongoClient(uri, **kwargs)
    client.admin.command("ping")
    return client[config.DB_NAME]


def make_session():
    s = requests.Session()
    s.headers["User-Agent"] = config.USER_AGENT
    return s


class RateLimiter:
    """Guarantees at least `interval` seconds between requests (one process = one limiter)."""

    def __init__(self, interval):
        self.interval = interval
        self._last = 0.0

    def wait(self):
        sleep_for = self._last + self.interval - time.monotonic()
        if sleep_for > 0:
            time.sleep(sleep_for)
        self._last = time.monotonic()
