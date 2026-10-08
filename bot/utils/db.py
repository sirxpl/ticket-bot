import os
import logging
import time

from pymongo import MongoClient

logger = logging.getLogger("db")
if not logger.handlers:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter('[%(asctime)s] [%(levelname)s] %(name)s: %(message)s'))
    logger.addHandler(h)
    logger.setLevel(logging.INFO)

_client = None
_db = None
_attempted = False
_last_attempt_at = 0.0
_RETRY_INTERVAL_SECONDS = 30


def get_db():
    """Return a MongoDB database handle if MONGODB_URI is configured and
    reachable, otherwise None. If MongoDB is configured but temporarily
    unavailable, connection attempts are retried periodically so persisted
    features can recover without a process restart. When it is not configured,
    callers may fall back to local JSON/HTML files.
    """
    global _client, _db, _attempted, _last_attempt_at

    if _db is not None:
        return _db
    uri = os.getenv("MONGODB_URI")
    if not uri:
        if not _attempted:
            logger.info("MONGODB_URI not set — using local file storage.")
        _attempted = True
        return None
    if (
        _attempted
        and time.monotonic() - _last_attempt_at < _RETRY_INTERVAL_SECONDS
    ):
        return None
    _attempted = True
    _last_attempt_at = time.monotonic()

    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=5000)
        try:
            db = client.get_default_database()
        except Exception:
            db = None
        if db is None:
            db = client["ticket_bot"]
        # round-trip now so a bad URI/credentials fails fast and falls back
        client.admin.command("ping")
        _client = client
        _db = db
        logger.info(f"Connected to MongoDB database '{db.name}'.")
        return _db
    except Exception as e:
        logger.warning(f"MongoDB connection failed, falling back to local files: {e}")
        _client = None
        _db = None
        return None
