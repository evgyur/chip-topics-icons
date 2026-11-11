import copy
from email.parser import BytesParser
from email.policy import default
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import socket
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw

from topics_icons.artwork import checked_svg, prepare, prepared_integrity
from topics_icons.cli import main, single_writer
from topics_icons.model import IconError, decimal_id, load, save, validate_topics
from topics_icons.telegram import Bot, BotError, Forum, JsonHTTP
from topics_icons.workflow import apply, binding, publish, verify

ROOT = Path(__file__).resolve().parent.parent
TARGET = "-1001234567890"  # Synthetic example only, not a real task target.


def fixture_manifest(directory, count=3):
    directory = Path(directory)
    assets = directory / "assets"
    assets.mkdir()
    base = load(ROOT / "examples/inventory.json")
    general = base["topics"][0]
    topics = [general]
    rows = []
    for n in range(count):
        tid = 101 + n
        topic = copy.deepcopy(base["topics"][min(n + 1, 3)])
        topic.update(id=tid, title="Synthetic topic " + str(tid))
        topics.append(topic)
        image = Image.new("RGBA", (100, 100))
        draw = ImageDraw.Draw(image)
        draw.rectangle((6, 6, 94, 94), fill=(20 + n % 200, 65, 145, 255))
        draw.line((15, 15, 85, 20 + n % 60), fill="white", width=7)
        draw.text((25, 50), str(n), fill="white")
        image.save(assets / (str(tid) + ".png"))
        rows.append({"topic_id": tid, "design_id": "original-" + str(tid), "kind": "thematic",
                     "asset": str(tid) + ".png", "provenance": {"source": "original synthetic test", "rights": "MIT"}})
    inventory = {"chat_id": TARGET, "topics": topics, "provider_count": len(topics)}
    manifest = prepare(inventory, {"chat_id": TARGET, "icons": rows}, assets, directory / "prepared")
    return manifest, inventory


class Backend:
    def __init__(self, inventory):
        self.topics = {t["id"]: copy.deepcopy(t) for t in inventory["topics"]}
        self.original = copy.deepcopy(self.topics)
        self.files = {}
        self.packs = {}
        self.edits = []
        self.upload_count = 0
        self.stale_exact = {}
        self.stale_inventory = False
        self.inventory_snapshot = None
        self.repeat_cursor = False
        self.bad_count = False
        self.wrong_pack_order = False
        self.adds = 0
        self.drop_add = False
        self.reject_after_add = False
        self.hide_applied = False

    def request(self, method, path, data, file=None):
        path, _, query = path.partition("?")
        if path == "/health":
            return {"status": "ok"}
        if path == "/me":
            return {"success": True, "data": json.dumps({"id": 100, "username": "FixtureOperator"})}
        if "/topics" in path:
            if path.endswith("/topics"):
                first = "offset_topic" not in query
                if first:
                    self.inventory_snapshot = copy.deepcopy(self.original if self.stale_inventory else self.topics)
                    self.stale_inventory = False
                snapshot = self.inventory_snapshot or self.topics
                values = list(snapshot.values())
                offset = 0 if first else 2
                page = values[offset:offset + 2] if len(values) <= 4 else values
                more = first and len(values) <= 4 and len(values) > 2
                if self.repeat_cursor:
                    more = True
                result = {"chat_id": TARGET, "telegram_count": len(values), "returned_count": len(page) + int(self.bad_count),
                          "topics": page, "may_have_more": more,
                          "next_offsets": {"offset_topic": 101, "offset_id": 0, "offset_date": 0} if more else None}
                return {"success": True, "data": result}
            parts = path.split("/")
            tid = int(parts[-2] if path.endswith("/icon") else parts[-1])
            if method == "PATCH":
                if set(data) != {"icon_custom_emoji_id", "expected_icon_custom_emoji_id"}:
                    raise AssertionError("Non-icon write fields")
                if self.topics[tid]["icon_custom_emoji_id"] != data["expected_icon_custom_emoji_id"]:
                    return {"success": False, "data": {"error": "conflict"}}
                old = copy.deepcopy(self.topics[tid])
                self.topics[tid]["icon_custom_emoji_id"] = data["icon_custom_emoji_id"]
                self.edits.append(tid)
                self.stale_exact[tid] = 2
                self.stale_inventory = True
                return {"success": False, "data": {"changed": True, "verified": False, "before": old, "after": old}}
            if self.hide_applied:
                return {"success": True, "data": copy.deepcopy(self.original[tid])}
            if self.stale_exact.get(tid, 0):
                self.stale_exact[tid] -= 1
                return {"success": True, "data": copy.deepcopy(self.original[tid])}
            return {"success": True, "data": copy.deepcopy(self.topics[tid])}
        api = path.rsplit("/", 1)[-1]
        if api == "getMe":
            result = {"id": 500, "username": "fixturebot"}
        elif api == "getChat":
            result = {"id": int(TARGET), "title": "Synthetic forum", "is_forum": True}
        elif api == "getChatMember":
            result = {"status": "administrator", "can_manage_topics": True}
        elif api == "uploadStickerFile":
            self.upload_count += 1
            assert isinstance(file, bytes)
            digest = hashlib.sha256(file).hexdigest()
            fid = "file-" + digest
            result = {"file_id": fid, "file_unique_id": "unique-" + digest}
            self.files[fid] = result
        elif api == "getStickerSet":
            if data["name"] not in self.packs:
                return {"ok": False, "error_code": 400, "description": "Bad Request: STICKERSET_INVALID"}
            result = copy.deepcopy(self.packs[data["name"]])
            if self.wrong_pack_order:
                result["stickers"].reverse()
        elif api in ("createNewStickerSet", "addStickerToSet"):
            if api == "createNewStickerSet":
                assert data["sticker_type"] == "custom_emoji" and data["needs_repainting"] is False
                assert len(data["stickers"]) <= 50
                assert data["name"] not in self.packs
                self.packs[data["name"]] = {"name": data["name"], "sticker_type": "custom_emoji", "stickers": []}
                stickers = data["stickers"]
            else:
                self.adds += 1
                if self.drop_add:
                    return {"ok": False, "error_code": 500, "description": "Synthetic failure"}
                stickers = [data["sticker"]]
            for row in stickers:
                uploaded = self.files[row["sticker"]]
                self.packs[data["name"]]["stickers"].append({**uploaded, "width": 100, "height": 100,
                    "custom_emoji_id": str(9000000000000 + sum(len(p["stickers"]) for p in self.packs.values()))})
            if api == "addStickerToSet" and self.reject_after_add:
                return {"ok": False, "error_code": 500, "description": "Synthetic ambiguous response"}
            result = True
        else:
            raise AssertionError("Unexpected fixture method " + api)
        return {"ok": True, "result": result}


class FixtureHTTP:
    def __init__(self, backend):
        self.backend = backend

    def request(self, method, path, body=None, headers=None):
        data = json.loads(body) if body else {}
        return self.backend.request(method, path, data)


class HTTPFixture:
    def __init__(self, backend):
        self.backend = backend

    def __enter__(self):
        backend = self.backend
        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            def log_message(self, format, *args):
                pass
            def do_GET(self):
                self.dispatch()
            def do_PATCH(self):
                self.dispatch()
            def do_POST(self):
                self.dispatch()
            def dispatch(self):
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length) if length else b""
                content_type = self.headers.get("Content-Type", "")
                file = None
                data = {}
                if content_type.startswith("multipart/form-data"):
                    message = BytesParser(policy=default).parsebytes(("Content-Type: " + content_type + "\r\nMIME-Version: 1.0\r\n\r\n").encode() + raw)
                    for part in message.iter_parts():
                        name = part.get_param("name", header="content-disposition")
                        value = part.get_payload(decode=True)
                        assert isinstance(value, bytes)
                        if part.get_filename():
                            file = value
                        else:
                            text = value.decode()
                            try:
                                data[name] = json.loads(text)
                            except ValueError:
                                data[name] = text
                elif raw:
                    data = json.loads(raw)
                result = backend.request(self.command, self.path, data, file)
                body = json.dumps(result).encode()
                self.send_response(400 if result.get("ok") is False else 200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.origin = "http://127.0.0.1:" + str(self.server.server_address[1])
        return self

    def __exit__(self, *_):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        assert not self.thread.is_alive()
        probe = socket.socket()
        probe.settimeout(.5)
        try:
            assert probe.connect_ex(self.server.server_address) != 0
        finally:
            probe.close()


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="topic-icons-test-")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.manifest, self.inventory = fixture_manifest(self.directory)
        self.backend = Backend(self.inventory)

    def clients(self):
        transport = FixtureHTTP(self.backend)
        return Bot("fixture", http=transport), Forum("http://127.0.0.1", TARGET, http=transport)

    def publication(self, bot=None, size=2):
        if bot is None:
            # File uploads need real multipart processing, tested via local HTTP.
            raise AssertionError("Use the HTTP fixture for publication")
        return publish(self.manifest, bot, 100, "fixturebot", "FixtureIcons", self.directory / "publication.json", TARGET, size, sleep=lambda _: None)

    def fake_publication(self):
        return {"binding": {"manifest_sha256": binding(self.manifest)}, "assignments": [
            {"topic_id": r["topic_id"], "emoji_id": str(8000000000000 + i)} for i, r in enumerate(self.manifest["icons"])]}

    def test_full_http_prepare_publish_apply_verify_resume(self):
        delays = []
        with HTTPFixture(self.backend) as local:
            bot = Bot("fixture", http=JsonHTTP(local.origin))
            forum = Forum(local.origin, TARGET)
            self.assertEqual(forum.identity("FixtureOperator")["username"], "FixtureOperator")
            inventory = forum.inventory()
            self.assertEqual(len(inventory["topics"]), 4)
            self.assertTrue(inventory["topics"][-1]["closed"])
            state = self.publication(bot)
            self.assertEqual(len(state["assignments"]), 3)
            self.assertEqual(len(self.backend.packs), 2)
            self.assertEqual(self.backend.upload_count, 3)
            self.publication(bot)
            self.assertEqual(self.backend.upload_count, 3, "Resume must not upload again")
            result = apply(self.manifest, state, forum, self.directory / "receipts.json", TARGET, sleep=delays.append)
            self.assertEqual(result["verified_icons"], 3)
            self.assertEqual(result["unique_emoji_ids"], 3)
            self.assertTrue(result["general_preserved"])
            self.assertTrue(result["other_fields_unchanged"])
            self.assertEqual(sorted(self.backend.edits), [101, 102, 103])
            self.assertTrue(delays, "Stale readback must be reconciled, not ignored")
            apply(self.manifest, state, forum, self.directory / "receipts.json", TARGET, sleep=lambda _: None)
            self.assertEqual(len(self.backend.edits), 3)
            # A forged success flag is not evidence: change an icon and require failure.
            self.backend.topics[101]["icon_custom_emoji_id"] = "123"
            with self.assertRaises(IconError):
                verify(self.manifest, state, forum, sleep=lambda _: None)
            bot.http.connection.close()
            forum.http.connection.close()

    def test_uncertain_pack_add_reconciles_and_resumes(self):
        large = self.directory / "large"
        large.mkdir()
        self.manifest, self.inventory = fixture_manifest(large, count=51)
        self.backend = Backend(self.inventory)
        self.backend.reject_after_add = True
        with HTTPFixture(self.backend) as local:
            bot = Bot("fixture", http=JsonHTTP(local.origin))
            state = self.publication(bot, size=80)
            self.assertEqual(len(state["assignments"]), 51)
            self.assertEqual(self.backend.adds, 1)
            self.publication(bot, size=80)
            self.assertEqual(self.backend.adds, 1)
            self.assertEqual(self.backend.upload_count, 51)
            bot.http.connection.close()

    def test_unconfirmed_pack_add_is_not_resent_on_resume(self):
        large = self.directory / "large"
        large.mkdir()
        self.manifest, self.inventory = fixture_manifest(large, count=51)
        self.backend = Backend(self.inventory)
        self.backend.drop_add = True
        with HTTPFixture(self.backend) as local:
            bot = Bot("fixture", http=JsonHTTP(local.origin))
            with self.assertRaises(IconError):
                self.publication(bot, size=80)
            self.assertEqual(self.backend.adds, 1)
            with self.assertRaises(IconError):
                self.publication(bot, size=80)
            self.assertEqual(self.backend.adds, 1)
            bot.http.connection.close()

    def test_pack_content_and_bot_identity_are_bound(self):
        with HTTPFixture(self.backend) as local:
            bot = Bot("fixture", http=JsonHTTP(local.origin))
            self.publication(bot)
            self.backend.wrong_pack_order = True
            with self.assertRaisesRegex(IconError, "order/content"):
                self.publication(bot)
            with self.assertRaisesRegex(IconError, "bot identity"):
                bot.identity(TARGET, "anotherbot")
            bot.http.connection.close()

    def test_no_edit_without_exact_confirmation(self):
        _, forum = self.clients()
        with self.assertRaises(IconError):
            apply(self.manifest, self.fake_publication(), forum, self.directory / "receipts.json", "-100999", sleep=lambda _: None)
        self.assertFalse(self.backend.edits)

    def test_conflicting_icon_stops(self):
        _, forum = self.clients()
        self.backend.topics[101]["icon_custom_emoji_id"] = "123"
        with self.assertRaisesRegex(IconError, "Concurrent"):
            apply(self.manifest, self.fake_publication(), forum, self.directory / "receipts.json", TARGET, sleep=lambda _: None)
        self.assertFalse(self.backend.edits)

    def test_uncertain_write_resume_never_blindly_resends(self):
        _, forum = self.clients()
        self.backend.hide_applied = True
        path = self.directory / "receipts.json"
        with self.assertRaises(IconError):
            apply(self.manifest, self.fake_publication(), forum, path, TARGET, sleep=lambda _: None)
        self.assertEqual(len(self.backend.edits), 1)
        with self.assertRaises(IconError):
            apply(self.manifest, self.fake_publication(), forum, path, TARGET, sleep=lambda _: None)
        self.assertEqual(len(self.backend.edits), 1)
        self.backend.hide_applied = False
        apply(self.manifest, self.fake_publication(), forum, path, TARGET, sleep=lambda _: None)
        self.assertEqual(len(self.backend.edits), 3)

    def test_non_icon_change_stops(self):
        _, forum = self.clients()
        self.backend.topics[102]["title"] = "Unexpected concurrent rename"
        with self.assertRaises(IconError):
            verify(self.manifest, self.fake_publication(), forum, sleep=lambda _: None)

    def test_general_change_stops(self):
        _, forum = self.clients()
        self.backend.topics[1]["icon_custom_emoji_id"] = "123"
        with self.assertRaisesRegex(IconError, "General"):
            verify(self.manifest, self.fake_publication(), forum, sleep=lambda _: None)
        with self.assertRaises(IconError):
            forum.edit(1, "123", "0")

    def test_duplicate_emoji_ids_rejected(self):
        _, forum = self.clients()
        state = self.fake_publication()
        state["assignments"][1]["emoji_id"] = state["assignments"][0]["emoji_id"]
        with self.assertRaises(IconError):
            apply(self.manifest, state, forum, self.directory / "receipts.json", TARGET, sleep=lambda _: None)
        self.assertFalse(self.backend.edits)

    def test_wrong_runtime_identity(self):
        _, forum = self.clients()
        with self.assertRaises(IconError):
            forum.identity("SomeoneElse")

    def test_page_count_and_repeated_cursor_rejected(self):
        _, forum = self.clients()
        self.backend.bad_count = True
        with self.assertRaises(IconError):
            forum.inventory()
        self.backend.bad_count = False
        self.backend.repeat_cursor = True
        with self.assertRaisesRegex(IconError, "cursor"):
            forum.inventory()

    def test_missing_general_rejected(self):
        with self.assertRaises(IconError):
            validate_topics(self.inventory["topics"][1:])

    def test_manifest_tampering_rejected(self):
        path = Path(self.manifest["icons"][0]["path"])
        path.write_bytes(b"changed")
        with self.assertRaises(IconError):
            prepared_integrity(self.manifest)

    def test_decoded_duplicate_assets_rejected(self):
        design = load(ROOT / "examples/design.json")
        for row in design["icons"]:
            row["asset"] = "101.png"
        with self.assertRaisesRegex(IconError, "Duplicate"):
            prepare(self.inventory, design, self.directory / "assets", self.directory / "duplicates")

    def test_path_escape_rejected(self):
        design = load(ROOT / "examples/design.json")
        design["icons"][0]["asset"] = "../outside.png"
        with self.assertRaises(IconError):
            prepare(self.inventory, design, self.directory / "assets", self.directory / "escape")

    def test_svg_active_content_rejected(self):
        for source in [b'<svg xmlns="http://www.w3.org/2000/svg"><script/></svg>',
                       b'<!DOCTYPE svg><svg xmlns="http://www.w3.org/2000/svg"/>',
                       b'<svg xmlns="http://www.w3.org/2000/svg"><path fill="url(https://example.com/a)"/></svg>',
                       b'<svg xmlns="http://www.w3.org/2000/svg"><image href="file:///etc/passwd"/></svg>']:
            with self.subTest(source=source), self.assertRaises(IconError):
                checked_svg(source)
        self.assertTrue(checked_svg((ROOT / "examples/assets/research.svg").read_bytes()))

    def test_decimal_ids_not_repaired(self):
        for value in ["01", "1.0", " 1", "-1", 123, True, "9223372036854775808"]:
            with self.subTest(value=value), self.assertRaises(IconError):
                decimal_id(value)
        self.assertEqual(decimal_id("9223372036854775807"), "9223372036854775807")

    def test_http_origin_binding(self):
        for origin in ["http://example.com", "https://example.com/path", "https://user:pass@example.com", "https://example.com?key=value"]:
            with self.subTest(origin=origin), self.assertRaises(IconError):
                JsonHTTP(origin)

    def test_single_writer_lock(self):
        path = self.directory / "state.json"
        with single_writer(path):
            with self.assertRaises(IconError):
                with single_writer(path):
                    pass

    def test_provider_errors_and_redirects_do_not_leak_or_follow(self):
        marker = "synthetic-sensitive-marker"
        class Response:
            status = 302
            def read(self, size):
                return b"{}"
        class Connection:
            def request(self, *args, **kwargs):
                pass
            def getresponse(self):
                return Response()
            def close(self):
                pass
        client = JsonHTTP("http://127.0.0.1")
        with patch.object(client, "connection", Connection()):
            with self.assertRaisesRegex(IconError, "redirect refused"):
                client.request("GET", "/read")
        class FailingConnection(Connection):
            def request(self, *args, **kwargs):
                raise OSError(marker)
        with patch.object(client, "connection", FailingConnection()):
            with self.assertRaises(IconError) as failure:
                client.request("POST", "/sensitive", b"sensitive-body")
        self.assertNotIn(marker, str(failure.exception))
        class RejectingHTTP:
            def request(self, *args, **kwargs):
                return {"ok": False, "error_code": 400, "description": marker}
        bot = Bot("fixture", http=RejectingHTTP())
        with self.assertRaises(BotError) as failure:
            bot.call("getMe")
        self.assertNotIn(marker, str(failure.exception))

    def test_local_validation_precedes_upload(self):
        with HTTPFixture(self.backend) as local:
            bot = Bot("fixture", http=JsonHTTP(local.origin))
            with self.assertRaisesRegex(IconError, "64 characters"):
                publish(self.manifest, bot, 100, "fixturebot", "A" * 65,
                        self.directory / "publication.json", TARGET, sleep=lambda _: None)
            self.assertEqual(self.backend.upload_count, 0)
            bot.http.connection.close()

    def test_private_atomic_state(self):
        path = self.directory / "state.json"
        save(path, {"example": True})
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(load(path), {"example": True})

    def test_preview_only_cli_has_no_transport(self):
        import contextlib
        capture = io.StringIO()
        manifest_path = self.directory / "prepared/manifest.json"
        inventory_path = self.directory / "input-inventory.json"
        design_path = self.directory / "input-design.json"
        save(inventory_path, self.inventory)
        design = load(ROOT / "examples/design.json")
        for row in design["icons"]:
            row["asset"] = str(row["topic_id"]) + ".png"
        save(design_path, design)
        with contextlib.redirect_stdout(capture):
            self.assertEqual(main(["prepare", "--inventory", str(inventory_path), "--design", str(design_path),
                "--assets", str(self.directory / "assets"), "--out", str(self.directory / "cli-output")]), 0)
            self.assertEqual(main(["check", "--manifest", str(manifest_path)]), 0)
        self.assertFalse(self.backend.edits)
        self.assertIn("Validated 3", capture.getvalue())

    def test_skill_contract_and_negative_case(self):
        spec = importlib.util.spec_from_file_location("validate_skill", ROOT / "scripts/validate_skill.py")
        assert spec is not None and spec.loader is not None
        validator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(validator)
        self.assertEqual(validator.validate(ROOT), [])
        # A contract-removal test changes only the root in an isolated fixture.
        candidate = self.directory / "skill"
        candidate.mkdir()
        text = (ROOT / "SKILL.md").read_text().replace("General", "RemovedInvariant")
        (candidate / "SKILL.md").write_text(text)
        self.assertIn("Missing root contract: General", validator.validate(candidate))


if __name__ == "__main__":
    unittest.main()
