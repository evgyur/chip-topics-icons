"""Bound-origin HTTP transports. Credentials never enter artifacts or exceptions."""
import http.client
import json
import re
import secrets
import urllib.parse

from .model import IconError, chat_id, validate_topics


class TransportError(IconError):
    pass


class JsonHTTP:
    def __init__(self, origin):
        url = urllib.parse.urlsplit(origin)
        loopback = url.hostname in ("localhost", "127.0.0.1", "::1")
        if url.username or url.password or url.path not in ("", "/") or url.query or url.fragment:
            raise IconError("API origin must not contain credentials, paths or query strings")
        if url.scheme != "https" and not (url.scheme == "http" and loopback):
            raise IconError("Use HTTPS, or HTTP on loopback only")
        if not url.hostname:
            raise IconError("API origin needs an explicit hostname")
        cls = http.client.HTTPSConnection if url.scheme == "https" else http.client.HTTPConnection
        self.connection = cls(url.hostname, url.port, timeout=60)

    def request(self, method, path, body=None, headers=None):
        if not path.startswith("/") or path.startswith("//"):
            raise IconError("API-relative paths only")
        try:
            self.connection.request(method, path, body=body, headers=headers or {})
            response = self.connection.getresponse()
            raw = response.read(8_000_001)
            status = response.status
            if len(raw) > 8_000_000:
                raise TransportError("API response exceeded the size limit")
            if 300 <= status < 400:
                raise TransportError("API redirect refused; credentials remain origin-bound")
            data = json.loads(raw)
        except (OSError, http.client.HTTPException, ValueError):
            self.connection.close()
            raise TransportError("API transport failed; reconcile uncertain writes before retry") from None
        if status >= 400 and not (isinstance(data, dict) and data.get("ok") is False):
            raise TransportError("API HTTP status " + str(status))
        return data


def unwrap(data):
    value = data.get("data", data)
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            raise IconError("Expected structured adapter data") from None
    return value


class Forum:
    def __init__(self, origin, target, token=None, http=None):
        self.target = chat_id(target)
        self.http = http or JsonHTTP(origin)
        self.headers = {"Content-Type": "application/json"}
        if token:
            self.headers["Authorization"] = "Bearer " + token

    def call(self, path, body=None):
        data = self.http.request("GET" if body is None else "PATCH", path,
                                 None if body is None else json.dumps(body).encode(), self.headers)
        value = unwrap(data)
        if data.get("success") is False:
            # An accepted edit can have a stale immediate readback. Preserve its
            # structured receipt; the workflow must independently reconcile it.
            if body is not None and isinstance(value, dict) and value.get("changed") is True:
                return value
            raise IconError("Forum adapter rejected the operation")
        return value

    def identity(self, expected_username):
        if not expected_username:
            raise IconError("Expected operator username is required")
        health = self.call("/health")
        if health.get("status") != "ok":
            raise IconError("Forum adapter is not healthy")
        me = self.call("/me")
        if me.get("username") != expected_username:
            raise IconError("Forum operator identity mismatch")
        return {"username": me["username"], "id": me.get("id")}

    def inventory(self):
        topics = {}
        offsets = {}
        cursors = set()
        provider_counts = set()
        for _ in range(1000):
            query = urllib.parse.urlencode({"limit": 100, **offsets})
            data = self.call("/chats/" + self.target + "/topics?" + query)
            if str(data["chat_id"]) != self.target:
                raise IconError("Inventory chat identity mismatch")
            page = data["topics"]
            if data["returned_count"] != len(page):
                raise IconError("Inventory page count mismatch")
            provider_counts.add(data["telegram_count"])
            if len(provider_counts) != 1:
                raise IconError("Forum count changed during pagination; take a fresh inventory")
            for topic in page:
                if topic["id"] in topics and topics[topic["id"]] != topic:
                    raise IconError("Topic changed during pagination")
                topics[topic["id"]] = topic
            if not data["may_have_more"]:
                break
            offsets = data.get("next_offsets")
            cursor = json.dumps(offsets, sort_keys=True)
            if not offsets or cursor in cursors:
                raise IconError("Missing or repeated pagination cursor")
            cursors.add(cursor)
        else:
            raise IconError("Inventory pagination limit reached")
        validate_topics(list(topics.values()))
        count = next(iter(provider_counts))
        # Some MTProto layers count General separately. No other discrepancy is accepted.
        if count not in (len(topics), len(topics) - 1):
            raise IconError("Complete inventory does not reconcile with provider count")
        return {"chat_id": self.target, "topics": list(topics.values()), "provider_count": count}

    def topic(self, tid):
        topic = self.call("/chats/" + self.target + "/topics/" + str(tid))
        if topic.get("id") != tid:
            raise IconError("Exact topic identity mismatch")
        return topic

    def edit(self, tid, new, old):
        if tid == 1:
            raise IconError("General cannot receive a custom icon")
        return self.call("/chats/" + self.target + "/topics/" + str(tid) + "/icon",
                         {"icon_custom_emoji_id": new, "expected_icon_custom_emoji_id": old})


class BotError(IconError):
    def __init__(self, method, code, missing_set=False):
        self.missing_set = missing_set
        super().__init__("Bot API " + method + " rejected (code " + str(code) + ")")


class Bot:
    def __init__(self, token, http=None):
        if not token or any(c.isspace() for c in token):
            raise IconError("Set TELEGRAM_BOT_TOKEN securely outside the repository")
        self.prefix = "/bot" + token + "/"
        self.http = http or JsonHTTP("https://api.telegram.org")

    def call(self, method, data=None, file=None):
        if not re.fullmatch(r"[A-Za-z]+", method):
            raise IconError("Invalid Bot API method")
        if file is None:
            body = json.dumps(data or {}).encode()
            headers = {"Content-Type": "application/json"}
        else:
            field, name, payload = file
            boundary = "icons" + secrets.token_hex(16)
            chunks = []
            for key, value in (data or {}).items():
                if not isinstance(value, str):
                    value = json.dumps(value)
                chunks.append(("--" + boundary + '\r\nContent-Disposition: form-data; name="' + key + '"\r\n\r\n' + value + "\r\n").encode())
            chunks.append(("--" + boundary + '\r\nContent-Disposition: form-data; name="' + field + '"; filename="' + name + '"\r\nContent-Type: image/webp\r\n\r\n').encode() + payload + b"\r\n")
            chunks.append(("--" + boundary + "--\r\n").encode())
            body = b"".join(chunks)
            headers = {"Content-Type": "multipart/form-data; boundary=" + boundary}
        data = self.http.request("POST", self.prefix + method, body, headers)
        if not data.get("ok"):
            # Never echo provider descriptions, request bodies or token-bearing URLs.
            missing = method == "getStickerSet" and "STICKERSET_INVALID" in data.get("description", "")
            raise BotError(method, data.get("error_code", "unknown"), missing_set=missing)
        return data["result"]

    def identity(self, target, expected_username):
        me = self.call("getMe")
        if me.get("username") != expected_username:
            raise IconError("Incumbent bot identity mismatch")
        chat = self.call("getChat", {"chat_id": target})
        if str(chat.get("id")) != target or chat.get("is_forum") is not True:
            raise IconError("Bot target is not the exact authorized forum")
        member = self.call("getChatMember", {"chat_id": target, "user_id": me["id"]})
        if member.get("status") not in ("creator", "administrator") or (member.get("status") != "creator" and not member.get("can_manage_topics")):
            raise IconError("Incumbent bot lacks topic administration rights")
        return {"username": me["username"], "id": me["id"], "chat_title": chat["title"]}
