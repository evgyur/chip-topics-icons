"""Resumable publication and icon-only application. No implicit live effects."""
import hashlib
import json
from pathlib import Path
import re
import time

from .artwork import prepared_integrity
from .model import IconError, decimal_id, non_icon, save, validate_manifest, validate_topics
from .telegram import BotError


def binding(manifest):
    return hashlib.sha256(json.dumps(manifest, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def wait_topic(forum, tid, expected, sleep=time.sleep):
    for delay in (0, .5, 1, 2, 4, 8, 16):
        if delay:
            sleep(delay)
        try:
            topic = forum.topic(tid)
        except IconError:
            continue
        if topic["icon_custom_emoji_id"] == expected:
            return topic
    raise IconError("Topic icon remains unverified; do not blindly repeat the write")


def require_confirmation(manifest, target):
    if target != manifest["chat_id"]:
        raise IconError("Explicit confirmation must match the exact manifest chat ID")


def sticker(file_id):
    return {"sticker": file_id, "format": "static", "emoji_list": ["🔹"]}


def check_pack(pack, group, uploads):
    if pack["sticker_type"] != "custom_emoji" or len(pack["stickers"]) > len(group):
        raise IconError("Pack type/count differs from this run")
    for item, row in zip(pack["stickers"], group):
        if item["file_unique_id"] != uploads[str(row["topic_id"])]["file_unique_id"]:
            raise IconError("Pack order/content differs from uploaded assets; preserve it and stop")
        if (item["width"], item["height"]) != (100, 100):
            raise IconError("Telegram pack contains invalid custom emoji dimensions")
        decimal_id(item["custom_emoji_id"])


def publish(manifest, bot, owner_id, bot_username, prefix, state_path, confirm_chat, pack_size=80, sleep=time.sleep):
    require_confirmation(manifest, confirm_chat)
    prepared_integrity(manifest)
    if type(owner_id) is not int or owner_id <= 0:
        raise IconError("Explicit sticker-set owner user ID is required")
    if not 1 <= pack_size <= 200 or not re.fullmatch(r"[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)*", prefix):
        raise IconError("Use a short alphanumeric pack prefix and a pack size from 1 to 200")
    names = [prefix + "_" + str(i + 1) + "_by_" + bot_username
             for i in range((len(manifest["icons"]) + pack_size - 1) // pack_size)]
    if any(len(name) > 64 for name in names):
        raise IconError("Sticker-set name exceeds 64 characters; choose a shorter prefix")
    identity = bot.identity(manifest["chat_id"], bot_username)
    config = {"manifest_sha256": binding(manifest), "chat_id": manifest["chat_id"], "owner_id": owner_id,
              "bot_id": identity["id"], "bot_username": bot_username, "prefix": prefix, "pack_size": pack_size}
    path = Path(state_path)
    if path.exists():
        state = json.loads(path.read_text(encoding="utf-8"))
        if state.get("binding") != config:
            raise IconError("Run state belongs to another manifest, owner, bot or pack configuration")
    else:
        state = {"binding": config, "uploads": {}, "packs": {}, "assignments": []}
        save(path, state)
    for row in manifest["icons"]:
        key = str(row["topic_id"])
        if key not in state["uploads"]:
            payload = Path(row["path"]).read_bytes()
            uploaded = bot.call("uploadStickerFile", {"user_id": owner_id, "sticker_format": "static"},
                                file=("sticker", "icon.webp", payload))
            state["uploads"][key] = {"file_id": uploaded["file_id"], "file_unique_id": uploaded["file_unique_id"]}
            save(path, state)
    assignments = []
    rows = manifest["icons"]
    for start in range(0, len(rows), pack_size):
        group = rows[start:start + pack_size]
        name = prefix + "_" + str(start // pack_size + 1) + "_by_" + bot_username
        if len(name) > 64:
            raise IconError("Sticker-set name exceeds 64 characters; choose a shorter prefix")
        planned = name in state["packs"]
        try:
            pack = bot.call("getStickerSet", {"name": name})
        except BotError as error:
            if not error.missing_set:
                raise
            if planned:
                raise IconError("Previously planned pack is absent; creation is unresolved, inspect before retry") from None
            state["packs"][name] = {"topic_ids": [r["topic_id"] for r in group], "status": "creating"}
            save(path, state)
            bot.call("createNewStickerSet", {"user_id": owner_id, "name": name, "title": "Topic Icons " + str(start // pack_size + 1),
                     "sticker_type": "custom_emoji", "needs_repainting": False,
                     "stickers": [sticker(state["uploads"][str(r["topic_id"])]["file_id"]) for r in group[:50]]})
            pack = bot.call("getStickerSet", {"name": name})
        else:
            if not planned:
                raise IconError("Pack name already exists outside this run; choose another prefix")
        check_pack(pack, group, state["uploads"])
        pending = state["packs"][name].get("pending_index")
        if pending is not None:
            for delay in (0, .5, 1, 2, 4, 8, 16):
                if len(pack["stickers"]) > pending:
                    break
                if delay:
                    sleep(delay)
                pack = bot.call("getStickerSet", {"name": name})
                check_pack(pack, group, state["uploads"])
            if len(pack["stickers"]) <= pending:
                raise IconError("Prior pack addition is unresolved; no mutation was resent")
            state["packs"][name].pop("pending_index", None)
            save(path, state)
        for idx in range(len(pack["stickers"]), len(group)):
            row = group[idx]
            state["packs"][name]["pending_index"] = idx
            save(path, state)
            try:
                bot.call("addStickerToSet", {"user_id": owner_id, "name": name,
                         "sticker": sticker(state["uploads"][str(row["topic_id"])]["file_id"])})
            except IconError:
                observed = bot.call("getStickerSet", {"name": name})
                check_pack(observed, group, state["uploads"])
                if len(observed["stickers"]) != idx + 1:
                    raise IconError("Uncertain pack add; no mutation was resent") from None
            # Read exact set after every addition before proceeding.
            pack = bot.call("getStickerSet", {"name": name})
            check_pack(pack, group, state["uploads"])
            if len(pack["stickers"]) != idx + 1:
                raise IconError("Pack add was not read back")
            state["packs"][name].pop("pending_index", None)
            save(path, state)
            sleep(.15)
        if len(pack["stickers"]) != len(group):
            raise IconError("Incomplete pack")
        state["packs"][name]["status"] = "verified"
        for row, item in zip(group, pack["stickers"]):
            assignments.append({"topic_id": row["topic_id"], "emoji_id": item["custom_emoji_id"],
                                "file_unique_id": item["file_unique_id"], "pack": name})
        save(path, state)
    for key in ("emoji_id", "file_unique_id"):
        if len({r[key] for r in assignments}) != len(rows):
            raise IconError("Published custom emoji are not unique")
    state["assignments"] = assignments
    save(path, state)
    return state


def apply(manifest, publication, forum, receipt_path, confirm_chat, sleep=time.sleep):
    require_confirmation(manifest, confirm_chat)
    original = validate_manifest(manifest)
    if forum.target != manifest["chat_id"] or publication["binding"]["manifest_sha256"] != binding(manifest):
        raise IconError("Forum/publication is bound to a different target or manifest")
    expected = {}
    for row in publication["assignments"]:
        tid = row["topic_id"]
        if tid in expected:
            raise IconError("Duplicate publication topic")
        expected[tid] = decimal_id(row["emoji_id"])
    if set(expected) != set(original) - {1} or len(set(expected.values())) != len(expected) or "0" in expected.values():
        raise IconError("Publication must cover all editable topics with unique nonzero emoji IDs")
    current = validate_topics(forum.inventory()["topics"])
    if set(current) != set(original):
        raise IconError("Topic set changed; reconcile inventory before editing")
    path = Path(receipt_path)
    if path.exists():
        receipts = json.loads(path.read_text(encoding="utf-8"))
        if receipts.get("manifest_sha256") != binding(manifest) or receipts.get("expected") != {str(k): v for k, v in expected.items()}:
            raise IconError("Receipt file belongs to another run")
    else:
        receipts = {"manifest_sha256": binding(manifest), "expected": {str(k): v for k, v in expected.items()}, "topics": {}}
    for tid, emoji in expected.items():
        before = forum.topic(tid)
        if non_icon(before) != non_icon(original[tid]):
            raise IconError("Non-icon topic metadata changed; stop rather than overwriting it")
        key = str(tid)
        if before["icon_custom_emoji_id"] != emoji:
            if key in receipts["topics"]:
                # Resume an uncertain write by observing it; never automatically resend.
                before = wait_topic(forum, tid, emoji, sleep)
            else:
                if before["icon_custom_emoji_id"] != original[tid]["icon_custom_emoji_id"]:
                    raise IconError("Concurrent icon change; take a fresh inventory")
                receipts["topics"][key] = {"status": "pending", "before": before,
                    "rollback": {"icon_custom_emoji_id": before["icon_custom_emoji_id"], "expected_icon_custom_emoji_id": emoji}}
                save(path, receipts)
                try:
                    forum.edit(tid, emoji, before["icon_custom_emoji_id"])
                except IconError:
                    pass  # Result can be uncertain; exact readback is the only completion proof.
                before = wait_topic(forum, tid, emoji, sleep)
        if non_icon(before) != non_icon(original[tid]):
            raise IconError("Unexpected non-icon metadata change after editing")
        entry = receipts["topics"].setdefault(key, {"before": original[tid],
            "rollback": {"icon_custom_emoji_id": original[tid]["icon_custom_emoji_id"], "expected_icon_custom_emoji_id": emoji}})
        entry.update({"status": "verified", "after": before})
        save(path, receipts)
    summary = verify(manifest, publication, forum, sleep)
    receipts["verification"] = summary
    save(path, receipts)
    return summary


def verify(manifest, publication, forum, sleep=time.sleep):
    prepared_integrity(manifest)
    original = validate_manifest(manifest)
    if forum.target != manifest["chat_id"] or publication["binding"]["manifest_sha256"] != binding(manifest):
        raise IconError("Verification target/binding mismatch")
    expected = {r["topic_id"]: decimal_id(r["emoji_id"]) for r in publication["assignments"]}
    if set(expected) != set(original) - {1} or len(expected) != len(publication["assignments"]) or len(set(expected.values())) != len(expected) or "0" in expected.values():
        raise IconError("Verification requires complete unique published assignments")
    for delay in (0, .5, 1, 2, 4, 8, 16):
        if delay:
            sleep(delay)
        latest = forum.inventory()
        if latest["chat_id"] != manifest["chat_id"]:
            raise IconError("Verification inventory chat mismatch")
        live = validate_topics(latest["topics"])
        if set(live) != set(original):
            raise IconError("Final topic count/identity changed")
        if any(non_icon(live[tid]) != non_icon(original[tid]) for tid in original):
            raise IconError("Non-icon topic metadata changed")
        if live[1] != original[1]:
            raise IconError("General was changed")
        if all(live[tid]["icon_custom_emoji_id"] == emoji for tid, emoji in expected.items()):
            return {"total_topics": len(live), "verified_icons": len(expected), "unique_emoji_ids": len(set(expected.values())),
                    "unique_decoded_images": len({r["pixel_sha256"] for r in manifest["icons"]}),
                    "general_preserved": True, "other_fields_unchanged": True}
    raise IconError("Full inventory is still stale or mismatched; do not claim completion")
