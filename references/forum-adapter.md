# Existing forum runtime contract

The helper operates an already-authenticated runtime. It does not install one, load session files, supply a Telethon login flow, start another user client, or grant permissions. If your agent already has equivalent MTProto tools, use those tools directly while preserving the same invariants. Do not migrate the runtime merely to match this example HTTP interface.

## API origin and authentication

`FORUM_API_URL` is the origin of the existing adapter, without a path, query or embedded credentials. HTTPS is required except for loopback HTTP. If needed, set `FORUM_API_TOKEN` securely in the environment; it is sent only as a Bearer header to that exact origin. Redirects are refused, including for Bot API requests. Never expose a user-session service on a public unauthenticated listener.

Responses may contain a `data` object or JSON-encoded `data` string. Exact operation errors must be returned as failed responses. Account objects must not be dumped to logs.

```text
GET /health
  {"status":"ok"}
GET /me
  {"success":true,"data":{"id":100,"username":"FixtureOperator"}}
```

The identity shown above is synthetic. The CLI requires the expected username explicitly and records no phone number.

## Complete forum inventory

```text
GET /chats/{canonical_chat_id}/topics?limit=100
GET /chats/{canonical_chat_id}/topics?limit=100&offset_topic=...&offset_id=...&offset_date=...
```

Data shape:

```json
{
  "chat_id": "-1001234567890",
  "telegram_count": 4,
  "returned_count": 2,
  "topics": [
    {"id":1,"title":"General","icon_color":7322096,"icon_custom_emoji_id":"0","closed":false,"hidden":false,"pinned":false},
    {"id":101,"title":"Research","icon_color":7322096,"icon_custom_emoji_id":"0","closed":false,"hidden":false,"pinned":true}
  ],
  "may_have_more": true,
  "next_offsets": {"offset_topic":101,"offset_id":0,"offset_date":0}
}
```

This is a synthetic shape example, not a complete inventory. `returned_count` must equal the actual page length. Follow the adapter's exact offsets; check repeated cursors and inconsistent pages. General must be included explicitly. Some MTProto layer results count General separately, so the helper permits the complete enumerated length to equal `telegram_count` or `telegram_count + 1`, but no other discrepancy. Duplicate topic entries across pages are accepted only when identical; do not silently omit closed topics. A changing provider count or inconsistent repeated topic requires a new snapshot.

MTProto implementations may use `messages.GetForumTopics` and `messages.GetForumTopicsByID`; verify the constructors/signatures in the runtime's own managed interpreter instead of copying an old library namespace. Reuse its existing client.

## Exact topic and icon-only edit

```text
GET /chats/{canonical_chat_id}/topics/{topic_id}
PATCH /chats/{canonical_chat_id}/topics/{topic_id}/icon
```

The exact GET returns one topic object matching the fields above. PATCH takes **only**:

```json
{"icon_custom_emoji_id":"1234567890123456789","expected_icon_custom_emoji_id":"0"}
```

The decimal value is illustrative, not an actual usable emoji ID. Reject unexpected fields, malformed decimal strings, signed-int64 overflow and General (`topic_id=1`). Enforce the exact authorized chat and icon operation, not a wildcard or generic message-send allowance.

Under the existing runtime's edit serialization, read the topic immediately before editing, compare its old icon with `expected_icon_custom_emoji_id`, and send only the new icon to Telegram. Do not send title/closed/hidden fields. Telegram itself does not provide an atomic compare-and-swap here: preserve that limitation and stop on observed conflicts.

Return structured `before`, `after`, `changed`, `verified` and rollback fields. A successful Telegram edit can precede a stale immediate readback. If `changed=true` but `verified=false`, the CLI retains the operation and performs bounded exact reads before any resend; it never fabricates success. A failed permission check must not masquerade as an accepted write.

## Why a separate bot is not a substitute

The incumbent bot supplies standard `getMe`, `getChat`, `getChatMember`, `uploadStickerFile`, `createNewStickerSet`, `addStickerToSet` and `getStickerSet` operations. Pack creation does not prove your user runtime can apply arbitrary custom emoji. General and custom-emoji eligibility depend on Telegram's live capabilities and privileges. Check the actual response and readback in the exact target; do not evade a denial by switching to another account or bot.
