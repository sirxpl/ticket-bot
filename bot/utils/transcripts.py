"""Standalone transcript presentation shared by saved archives and the public preview."""
import datetime
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from jinja2 import Environment, FileSystemLoader, select_autoescape

ROOT = Path(__file__).resolve().parents[1]


@lru_cache(maxsize=1)
def _environment():
    return Environment(loader=FileSystemLoader(ROOT / "templates"), autoescape=select_autoescape(["html"]))


@lru_cache(maxsize=1)
def _assets():
    return ((ROOT / "static/css/transcripts.css").read_text(encoding="utf-8"),
            (ROOT / "static/js/transcripts.js").read_text(encoding="utf-8"))


def _url(value):
    value = str(value or "").strip()
    try:
        parsed = urlsplit(value)
    except ValueError:
        return ""
    return value if parsed.scheme.lower() in {"https", "http"} and parsed.netloc and not parsed.username and not parsed.password else ""


def _time(value):
    raw = str(value or "")
    try:
        stamp = datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=datetime.timezone.utc)
        stamp = stamp.astimezone(datetime.timezone.utc)
        return {"iso": stamp.isoformat(), "day": stamp.strftime("%B %d, %Y"),
                "label": stamp.strftime("%H:%M UTC"), "full": stamp.strftime("%b %d, %Y · %H:%M UTC")}
    except (ValueError, TypeError):
        return {"iso": "", "day": "Conversation", "label": raw or "Time not recorded", "full": raw or "Time not recorded"}


def _embed(raw):
    result = dict(raw)
    try:
        color = int(raw.get("color", 0x9FADFF))
    except (ValueError, TypeError):
        color = 0x9FADFF
    result["color_hex"] = f"#{max(0, min(color, 0xFFFFFF)):06x}"
    for key in ("url", "thumbnail_url", "author_icon", "image_url", "footer_icon"):
        result[key] = _url(raw.get(key))
    return result


def render_transcript(messages, channel_name, ticket_meta, generated_at_iso, filename,
                      omitted_messages=False, *, preview=False, back_url=""):
    """Render a portable HTML file; only trusted local CSS/JS bypass escaping."""
    prepared = []
    participants = set()
    attachment_count = 0
    previous_day = None
    for index, raw in enumerate(messages):
        message = dict(raw)
        author = str(raw.get("author_name") or "Unknown member")
        message.update(author_name=author, initials=author[:2].upper(),
                       avatar_url=_url(raw.get("avatar_url")), time=_time(raw.get("ts")), number=index + 1)
        message["day_start"] = message["time"]["day"] != previous_day
        previous_day = message["time"]["day"]
        participants.add(str(raw.get("author_id") or author))
        message["attachments"] = [{**item, "url": _url(item.get("url"))} for item in raw.get("attachments") or []]
        attachment_count += len(message["attachments"])
        message["embeds"] = [_embed(item) for item in raw.get("embeds") or []]
        message["components"] = [[{**item,
            "style": item.get("style") if item.get("style") in {"primary", "secondary", "success", "danger", "link"} else "secondary",
            "url": _url(item.get("url"))} for item in row] for row in raw.get("components") or []]
        prepared.append(message)
    css, script = _assets()
    meta = ticket_meta or {}
    template = _environment().get_template("preview_transcript.html" if preview else "transcript.html")
    return template.render(messages=prepared, channel_name=channel_name, ticket_meta=meta,
                           creator=meta.get("creator") or {}, fields=meta.get("fields") or {},
                           generated_at=_time(generated_at_iso), filename=filename,
                           participant_count=len(participants), attachment_count=attachment_count,
                           omitted_messages=omitted_messages, preview=preview, back_url=back_url,
                           transcript_css=css, transcript_script=script)


def theme_saved_transcript(document):
    """Restyle this bot's earlier HTML format on read, without rewriting saved records."""
    if 'data-transcript-theme="liquid-glass"' in document:
        return document
    # Match our old renderer, not arbitrary imported/exporter documents.
    if not all(marker in document for marker in ('<title>Transcript - ', '<div class="container">', '.discord-embed{')):
        return document
    if "<body>" not in document or "</head>" not in document:
        return document
    css, _ = _assets()
    document = document.replace("<body>", '<body class="transcript-page transcript-legacy" data-transcript-theme="liquid-glass">', 1)
    return document.replace("</head>", f'<style id="transcript-glass-compatibility">{css}</style></head>', 1)


def render_preview_transcript(back_url=""):
    """Fictional sample that uses the exact same renderer as saved conversations."""
    messages = [
        {"author_name": "Carry Ticket Bot", "author_id": "demo-bot", "is_bot": True,
         "ts": "2026-09-07T13:41:00Z", "embeds": [{"title": "Your carry ticket is ready", "color": 11120895,
         "description": "Thanks for reaching out. A carry team member will join you shortly.",
         "fields": [{"name": "Category", "value": "Standard Carry", "inline": True},
                    {"name": "Ticket", "value": "#1041", "inline": True}]}],
         "components": [[{"style": "secondary", "label": "Close ticket"}]]},
        {"author_name": "krayon10", "author_id": "demo-applicant", "ts": "2026-09-07T13:42:00Z",
         "content": "Hi! I would like to request help with a Standard Carry. My timezone is EST and I can join a private server."},
        {"author_name": "tdsash", "author_id": "demo-staff", "ts": "2026-09-07T13:44:00Z",
         "content": "Sounds good! I can help with that. Please keep the ticket open while we get the private server ready."},
        {"author_name": "krayon10", "author_id": "demo-applicant", "ts": "2026-09-07T13:45:00Z",
         "content": "Perfect, thank you! I have my strategy notes ready.",
         "attachments": [{"filename": "strategy-notes.txt", "url": "", "is_image": False}]},
        {"author_name": "tdsash", "author_id": "demo-staff", "ts": "2026-09-07T14:12:00Z",
         "content": "All done — thanks for joining us. Have a great rest of your day!"},
    ]
    return render_transcript(messages, "standard-carry-1041", {
        "ticket_number": 1041, "creator": {"name": "krayon10"},
        "fields": {"timezone": "EST", "can_join": "Yes"},
    }, "2026-09-07T14:13:00Z", "sample-ticket-1041.html", preview=True, back_url=back_url)
