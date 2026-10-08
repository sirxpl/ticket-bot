import datetime
import json
import logging
import os
import threading
import uuid

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from utils.db import get_db

logger = logging.getLogger("carry_applications")
DATA_DIR = "data"
APPLICATIONS_FILE = os.path.join(DATA_DIR, "carry_applications.json")
DENIAL_COOLDOWN = datetime.timedelta(days=14)
_file_lock = threading.RLock()
_active_user_index_ready = False

os.makedirs(DATA_DIR, exist_ok=True)


class ApplicationStateError(ValueError):
    def __init__(self, code, available_at=None):
        super().__init__(code)
        self.code = code
        self.available_at = available_at


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def _timestamp(value=None):
    return (value or _now()).isoformat()


def _read_file():
    try:
        with open(APPLICATIONS_FILE, encoding="utf-8") as file:
            value = json.load(file)
        return value if isinstance(value, list) else []
    except FileNotFoundError:
        return []
    except (OSError, json.JSONDecodeError):
        logger.exception("Failed to read carry application storage")
        raise


def _write_file(applications):
    temporary_path = f"{APPLICATIONS_FILE}.tmp"
    with open(temporary_path, "w", encoding="utf-8") as file:
        json.dump(applications, file, indent=2)
        file.flush()
        os.fsync(file.fileno())
    os.replace(temporary_path, APPLICATIONS_FILE)


def _mongo_collection():
    global _active_user_index_ready
    db = get_db()
    if db is None:
        if os.getenv("MONGODB_URI"):
            raise RuntimeError(
                "MONGODB_URI is set but MongoDB is unavailable; refusing to store "
                "Carry Team applications in local files."
            )
        return None
    if not _active_user_index_ready:
        try:
            db.carry_applications.create_index(
                "active_user",
                unique=True,
                partialFilterExpression={"active_user": {"$type": "string"}},
            )
            _active_user_index_ready = True
        except Exception:
            logger.exception("Failed to ensure the carry application user index")
            raise
    return db.carry_applications


def _clean(record):
    if not record:
        return None
    result = dict(record)
    result.pop("_id", None)
    result.pop("active_user", None)
    result["application_id"] = str(record.get("_id") or record.get("application_id"))
    return result


def _get_user_applications(user_id):
    uid = str(user_id)
    collection = _mongo_collection()
    if collection is not None:
        try:
            return [_clean(item) for item in collection.find({"user_id": uid})]
        except Exception:
            logger.exception("Failed to read applications for user=%s", uid)
            raise
    with _file_lock:
        return [
            dict(item)
            for item in _read_file()
            if str(item.get("user_id")) == uid
        ]


def get_latest_application_for_user(user_id):
    applications = _get_user_applications(user_id)
    return max(applications, key=lambda item: item.get("submitted_at", ""), default=None)


def _cooldown_end(application):
    decided_at = datetime.datetime.fromisoformat(application["decided_at"])
    return decided_at + DENIAL_COOLDOWN


def submit_application(user_id, username, answers, rules_accepted_at):
    uid = str(user_id)
    latest = get_latest_application_for_user(uid)
    if latest:
        if latest.get("status") == "pending":
            raise ApplicationStateError("pending")
        if latest.get("status") == "accepted":
            raise ApplicationStateError("accepted")
        if latest.get("status") == "denied":
            available_at = _cooldown_end(latest)
            if _now() < available_at:
                raise ApplicationStateError("cooldown", available_at)

    application_id = uuid.uuid4().hex
    record = {
        "application_id": application_id,
        "user_id": uid,
        "username": str(username or ""),
        "answers": dict(answers),
        "status": "pending",
        "submitted_at": _timestamp(),
        "rules_accepted_at": str(rules_accepted_at),
        "decided_at": None,
        "decided_by": None,
        "decision_reason": None,
        "notification_status": "pending",
        "notification_attempts": 0,
        "notification_next_attempt_at": 0,
        "notification_error": None,
        "bot_message_id": None,
        "webhook_message_id": None,
    }

    collection = _mongo_collection()
    if collection is not None:
        document = {"_id": application_id, "active_user": uid, **record}
        try:
            collection.insert_one(document)
        except DuplicateKeyError as error:
            latest = get_latest_application_for_user(uid)
            if latest and latest.get("status") == "pending":
                raise ApplicationStateError("pending") from error
            if latest and latest.get("status") == "accepted":
                raise ApplicationStateError("accepted") from error
            raise
        except Exception:
            logger.exception("Failed to save carry application id=%s", application_id)
            raise
        return record

    with _file_lock:
        applications = _read_file()
        latest = max(
            (item for item in applications if str(item.get("user_id")) == uid),
            key=lambda item: item.get("submitted_at", ""),
            default=None,
        )
        if latest:
            if latest.get("status") in {"pending", "accepted"}:
                raise ApplicationStateError(latest["status"])
            if latest.get("status") == "denied":
                available_at = _cooldown_end(latest)
                if _now() < available_at:
                    raise ApplicationStateError("cooldown", available_at)
        applications.append(record)
        _write_file(applications)
    return record


def get_application(application_id):
    app_id = str(application_id)
    collection = _mongo_collection()
    if collection is not None:
        try:
            return _clean(collection.find_one({"_id": app_id}))
        except Exception:
            logger.exception("Failed to read carry application id=%s", app_id)
            raise
    with _file_lock:
        return next(
            (
                dict(item)
                for item in _read_file()
                if str(item.get("application_id")) == app_id
            ),
            None,
        )


def get_pending_review_applications():
    collection = _mongo_collection()
    if collection is not None:
        try:
            return [
                _clean(item)
                for item in collection.find(
                    {"status": "pending", "bot_message_id": {"$ne": None}}
                )
            ]
        except Exception:
            logger.exception("Failed to list carry applications awaiting review")
            raise
    with _file_lock:
        return [
            dict(item)
            for item in _read_file()
            if item.get("status") == "pending" and item.get("bot_message_id")
        ]


def get_queued_application_notifications():
    collection = _mongo_collection()
    now = _now().timestamp()
    if collection is not None:
        try:
            return [
                _clean(item)
                for item in collection.find(
                    {
                        "status": "pending",
                        "notification_status": {"$ne": "sent"},
                        "notification_next_attempt_at": {"$lte": now},
                    }
                ).sort("submitted_at", 1).limit(25)
            ]
        except Exception:
            logger.exception("Failed to list queued carry application notices")
            raise
    with _file_lock:
        return [
            dict(item)
            for item in _read_file()
            if item.get("status") == "pending"
            and item.get("notification_status") != "sent"
            and float(item.get("notification_next_attempt_at") or 0) <= now
        ][:25]


def reset_pending_application_notifications():
    collection = _mongo_collection()
    if collection is not None:
        try:
            collection.update_many(
                {
                    "status": "pending",
                    "notification_status": {"$ne": "sent"},
                },
                {"$set": {"notification_next_attempt_at": 0}},
            )
            return
        except Exception:
            logger.exception("Failed to refresh pending application notifications")
            raise
    with _file_lock:
        applications = _read_file()
        changed = False
        for item in applications:
            if (
                item.get("status") == "pending"
                and item.get("notification_status") != "sent"
            ):
                item["notification_next_attempt_at"] = 0
                changed = True
        if changed:
            _write_file(applications)


def update_application_notification(application_id, **updates):
    app_id = str(application_id)
    collection = _mongo_collection()
    if collection is not None:
        try:
            return collection.update_one(
                {"_id": app_id, "status": "pending"},
                {"$set": updates},
            ).modified_count > 0
        except Exception:
            logger.exception("Failed to update application notification id=%s", app_id)
            raise
    with _file_lock:
        applications = _read_file()
        for item in applications:
            if str(item.get("application_id")) == app_id and item.get("status") == "pending":
                item.update(updates)
                _write_file(applications)
                return True
    return False


def decide_application(application_id, status, reason, decided_by):
    if status not in {"accepted", "denied"}:
        raise ValueError("Application verdict must be accepted or denied.")
    app_id = str(application_id)
    now = _timestamp()
    updates = {
        "status": status,
        "decision_reason": str(reason).strip(),
        "decided_at": now,
        "decided_by": str(decided_by),
    }
    collection = _mongo_collection()
    if collection is not None:
        update = {"$set": updates}
        if status == "denied":
            update["$unset"] = {"active_user": ""}
        try:
            result = collection.find_one_and_update(
                {"_id": app_id, "status": "pending"},
                update,
                return_document=ReturnDocument.AFTER,
            )
            return _clean(result)
        except Exception:
            logger.exception("Failed to decide carry application id=%s", app_id)
            raise
    with _file_lock:
        applications = _read_file()
        for item in applications:
            if str(item.get("application_id")) == app_id and item.get("status") == "pending":
                item.update(updates)
                _write_file(applications)
                return dict(item)
    return None
