"""Validation and private run-state primitives; no network effects."""
import json
import os
from pathlib import Path
import re
import tempfile


class IconError(Exception):
    """Safe, operator-facing failure (must not contain credentials)."""


def decimal_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"0|[1-9][0-9]{0,18}", value):
        raise IconError("Emoji IDs must be canonical decimal strings")
    if int(value) > 9223372036854775807:
        raise IconError("Emoji ID exceeds signed int64")
    return value


def chat_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"-100[1-9][0-9]*", value):
        raise IconError("An exact canonical -100 supergroup chat ID is required")
    return value


def positive_id(value):
    if type(value) is not int or value < 1:
        raise IconError("Topic IDs must be positive integers")
    return value


def validate_topics(topics):
    if not isinstance(topics, list):
        raise IconError("Expected a topic list")
    ids = set()
    for t in topics:
        tid = positive_id(t["id"])
        if tid in ids:
            raise IconError("Duplicate topic in inventory")
        ids.add(tid)
        if not isinstance(t.get("title"), str) or not 1 <= len(t["title"]) <= 128:
            raise IconError("Every topic needs its exact title")
        decimal_id(t["icon_custom_emoji_id"])
        for field in ("closed", "hidden", "pinned"):
            if type(t.get(field)) is not bool:
                raise IconError("Inventory must include closed, hidden and pinned state")
        if "icon_color" not in t:
            raise IconError("Inventory must include icon_color")
    if 1 not in ids:
        raise IconError("Complete inventory must explicitly include General (ID 1)")
    return {t["id"]: t for t in topics}


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save(path, data):
    """Atomic private JSON state; never place run state inside a public checkout."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=".icons-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def non_icon(topic):
    return {key: value for key, value in topic.items() if key != "icon_custom_emoji_id"}


def validate_manifest(manifest):
    chat_id(manifest["chat_id"])
    original = validate_topics(manifest["topics"])
    entries = manifest["icons"]
    editable = set(original) - {1}
    if len(entries) != len(editable) or {positive_id(r["topic_id"]) for r in entries} != editable:
        raise IconError("Exactly one prepared icon per editable topic is required")
    for key in ("design_id", "pixel_sha256", "file_sha256"):
        values = [r[key] for r in entries]
        if not all(isinstance(v, str) and v for v in values) or len(set(values)) != len(entries):
            raise IconError("Duplicate or missing " + key)
    for row in entries:
        if row["title"] != original[row["topic_id"]]["title"]:
            raise IconError("Manifest title differs from exact inventory title")
        if row["old_icon"] != original[row["topic_id"]]["icon_custom_emoji_id"]:
            raise IconError("Manifest prior icon differs from inventory")
    return original
