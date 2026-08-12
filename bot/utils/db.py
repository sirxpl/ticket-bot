import os
import logging

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


def get_db():
    """Return a MongoDB database handle if MONGODB_URI is configured and
    reachable, otherwise None. The connection is only attempted once per
    process; if it fails (or the env var is missing) every storage function
    falls back to local JSON/HTML files instead, so the app keeps working
    either way.
    """
    global _client, _db, _attempted

    if _db is not None:
        return _db
    if _attempted:
        return None
    _attempted = True

    uri = os.getenv("MONGODB_URI")
    if not uri:
        logger.info("MONGODB_URI not set — using local file storage.")
        return None

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
