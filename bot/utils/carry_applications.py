import datetime
import json
import logging
import os
import threading
import uuid

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from utils.db import get_db
from utils.storage import get_settings, save_settings

logger = logging.getLogger("carry_applications")
DATA_DIR = "data"
APPLICATIONS_FILE = os.path.join(DATA_DIR, "carry_applications.json")
DEFAULT_DENIAL_COOLDOWN_DAYS = 14
_file_lock = threading.RLock()
_active_user_index_ready = False

os.makedirs(DATA_DIR, exist_ok=True)


class ApplicationStateError(ValueError):
    def __init__(self, code, available_at=None, *, application=None, accepting_responses=True):
        super().__init__(code)
        self.code = code
        self.available_at = available_at
        self.application = application
        self.accepting_responses = accepting_responses


def _configured_database():
    db = get_db()
    if db is None and os.getenv("MONGODB_URI"):
        raise RuntimeError("MongoDB is unavailable; Carry Team application data was not changed.")
    return db


def get_application_config():
    """Read application policy strictly so an outage cannot reopen a closed form."""
    db = _configured_database()
    if db is not None:
        settings = db.bot_settings.find_one(
            {"_id": "singleton"}, {"carry_application_config": 1}
        ) or {}
    else:
        settings = get_settings()
    config = settings.get("carry_application_config") or {}
    days = config.get("denial_cooldown_days", DEFAULT_DENIAL_COOLDOWN_DAYS)
    if type(days) is not int or not 0 <= days <= 365:
        days = DEFAULT_DENIAL_COOLDOWN_DAYS
    return {
        "accepting_responses": config.get("accepting_responses", True) is True,
        "denial_cooldown_days": days,
    }


def save_application_config(accepting_responses, denial_cooldown_days):
    if type(accepting_responses) is not bool:
        raise ValueError("Choose whether applications accept responses.")
    if type(denial_cooldown_days) is not int or not 0 <= denial_cooldown_days <= 365:
        raise ValueError("The denial cooldown must be a whole number from 0 to 365 days.")
    config = {
        "accepting_responses": accepting_responses,
        "denial_cooldown_days": denial_cooldown_days,
    }
    db = _configured_database()
    if db is not None:
        db.bot_settings.update_one(
            {"_id": "singleton"}, {"$set": {"carry_application_config": config}}, upsert=True
        )
    else:
        settings = get_settings()
        settings["carry_application_config"] = config
        save_settings(settings)
    return config


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
    db = _configured_database()
    if db is None:
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


def get_application_cooldown_end(application, config=None):
    if not application or application.get("status") != "denied" or application.get("cooldown_cleared_at"):
        return None
    decided_at = datetime.datetime.fromisoformat(application["decided_at"])
    if decided_at.tzinfo is None:
        decided_at = decided_at.replace(tzinfo=datetime.timezone.utc)
    config = config if config is not None else get_application_config()
    return decided_at + datetime.timedelta(days=config["denial_cooldown_days"])


def _check_application_state(latest, config):
    context = {"application": latest, "accepting_responses": config["accepting_responses"]}
    if latest:
        if latest.get("status") in {"pending", "accepted"}:
            raise ApplicationStateError(latest["status"], **context)
        available_at = get_application_cooldown_end(latest, config)
        if available_at and _now() < available_at:
            raise ApplicationStateError("cooldown", available_at, **context)
    if not config["accepting_responses"]:
        raise ApplicationStateError("closed", **context)


def check_application_available(user_id):
    _check_application_state(get_latest_application_for_user(user_id), get_application_config())


def submit_application(user_id, username, answers, rules_accepted_at):
    uid = str(user_id)
    latest = get_latest_application_for_user(uid)
    config = get_application_config()
    _check_application_state(latest, config)

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
        "confirmation_dm_status": "pending",
        "confirmation_dm_attempts": 0,
        "confirmation_dm_error": None,
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
            if latest and latest.get("status") in {"pending", "accepted"}:
                raise ApplicationStateError(latest["status"], application=latest,
                                            accepting_responses=config["accepting_responses"]) from error
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
        _check_application_state(latest, get_application_config())
        applications.append(record)
        _write_file(applications)
    return record


def get_application_page(page=1, status="all", per_page=25):
    query = {"status": status} if status != "all" else {}
    collection = _mongo_collection()
    if collection is not None:
        total = collection.count_documents(query)
        pages = max(1, (total + per_page - 1) // per_page)
        page = max(1, min(page, pages))
        items = [_clean(item) for item in collection.find(query, {"answers": 0})
                 .sort([("submitted_at", -1), ("_id", -1)]).skip((page - 1) * per_page).limit(per_page)]
    else:
        with _file_lock:
            items = [dict(item) for item in _read_file()
                     if status == "all" or item.get("status") == status]
        items.sort(key=lambda item: (item.get("submitted_at", ""), item["application_id"]), reverse=True)
        total = len(items)
        pages = max(1, (total + per_page - 1) // per_page)
        page = max(1, min(page, pages))
        items = items[(page - 1) * per_page:page * per_page]
    return {"items": items, "page": page, "pages": pages, "total": total}


def manage_applications(application_ids, action, manager_id):
    """Change only the selected records in the expected state; retain staff history."""
    ids = list(dict.fromkeys(str(value) for value in application_ids))
    if not ids or len(ids) > 100:
        raise ValueError("Select between 1 and 100 applications.")
    now = _timestamp()
    if action == "remove_accepted":
        expected_status = "accepted"
        updates = {"status": "removed", "removed_at": now, "removed_by": str(manager_id),
                   "confirmation_dm_status": "cancelled"}
    elif action == "clear_cooldowns":
        expected_status = "denied"
        updates = {"cooldown_cleared_at": now, "cooldown_cleared_by": str(manager_id)}
    else:
        raise ValueError("Choose a valid application action.")
    collection = _mongo_collection()
    if collection is not None:
        query = {"_id": {"$in": ids}, "status": expected_status}
        if action == "clear_cooldowns":
            query["cooldown_cleared_at"] = None
        update = {"$set": updates}
        if action == "remove_accepted":
            update["$unset"] = {"active_user": ""}
        return collection.update_many(query, update).modified_count
    with _file_lock:
        applications = _read_file()
        changed = 0
        for item in applications:
            if item.get("application_id") not in ids or item.get("status") != expected_status:
                continue
            if action == "clear_cooldowns" and item.get("cooldown_cleared_at"):
                continue
            item.update(updates)
            changed += 1
        if changed:
            _write_file(applications)
    return changed


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
                        "notification_next_attempt_at": {"$lte": now},
                        "$or": [
                            {
                                "status": "pending",
                                "notification_status": {"$ne": "sent"},
                            },
                            {"confirmation_dm_status": "pending"},
                        ],
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
            if (
                (
                    item.get("status") == "pending"
                    and item.get("notification_status") != "sent"
                )
                or item.get("confirmation_dm_status") == "pending"
            )
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
            collection.update_many(
                {"confirmation_dm_status": "pending"},
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
                (
                    item.get("status") == "pending"
                    and item.get("notification_status") != "sent"
                )
                or item.get("confirmation_dm_status") == "pending"
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
                {"_id": app_id},
                {"$set": updates},
            ).modified_count > 0
        except Exception:
            logger.exception("Failed to update application notification id=%s", app_id)
            raise
    with _file_lock:
        applications = _read_file()
        for item in applications:
            if str(item.get("application_id")) == app_id:
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

