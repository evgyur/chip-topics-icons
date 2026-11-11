# CLI and private run files

Run all commands from the repository root using Python 3.11+ with `requirements.txt` installed. Output must live outside the package. Every example below uses explicit placeholders: substitute your own authorized values, not an arbitrary chat that happens to match an example.

## Environment

Supply these from your secret manager or an already-protected local environment, never via chat or committed files:

| Name | Purpose |
| --- | --- |
| `FORUM_API_URL` | Existing authenticated forum adapter origin; no path or embedded credentials |
| `FORUM_API_TOKEN` | Optional Bearer credential for that exact adapter |
| `TELEGRAM_BOT_TOKEN` | Incumbent bot credential, used only for `api.telegram.org` |

The helper does not discover or read private agent profiles, `.env` files, session files, SSH credentials or another runtime's token cache. No secrets are copied into state. The operator sets `CHAT`, `ACCOUNT`, `BOT`, `OWNER` and `RUN` as local shell variables: exact canonical chat ID, expected user-runtime username, exact incumbent bot username without `@`, sticker-set owner's user ID, and a private run directory outside this checkout.

Create the run directory with private permissions and ensure it is not inside a public Git repository. File writes are atomic and mode 0600; one writer per run directory is enforced on Unix. Do not share one run directory between operators or use it on a filesystem where local advisory locks are not reliable.

## 1. Inventory (Telegram read-only)

```sh
python -m topics_icons.cli inventory \
  --chat "$CHAT" --account "$ACCOUNT" --out "$RUN/inventory.json"
```

Verify the account and forum before authoring a design. Include all topics, General, old icons and non-icon fields. When messages are needed for classification, use your existing authorized tools with bounded reads; do not save private bodies in this package.

## 2. Design and prepare (offline)

Author a `design.json` in your private run directory using the shape in [the synthetic example](../examples/design.json). Each editable topic needs a distinct `design_id`, exact `topic_id`, `kind` (`brand` or `thematic`), relative `asset` and provenance containing `source` and `rights`; brands additionally require `identity`. Add variant, license, guideline URL and source commit when available. Assets must stay inside the explicit assets root. No entry is allowed for General.

Use approved local logos or retrieve only the required exact public brand assets. This package intentionally does not bundle a private brand library or silently select fuzzy logo matches. Its built-in examples are original MIT geometric artwork, not a universal icon catalog. Your agent can use its existing trusted icon/logo tools and record attribution and licensing locally.

```sh
python -m topics_icons.cli prepare \
  --inventory "$RUN/inventory.json" --design "$RUN/design.json" \
  --assets "$RUN/assets" --out "$RUN/prepared"
python -m topics_icons.cli check --manifest "$RUN/prepared/manifest.json"
```

`prepare` refuses a nonempty output directory to preserve prior run state. Do not delete a previous live run just to rerender; use a new version directory and reconcile its manifest before any new publication. The manifest binds exact topic titles/prior icons, source hashes, decoded pixel hashes, file hashes and private local paths. Lossless WebP outputs are 100×100 and under 512 kB. SVG input needs `rsvg-convert`; invalid/active SVGs fail rather than having their safety checks disabled.

Inspect **every** `contact-*.png` and `list-mockup-*.png` with vision. All labels remain exact; long labels increase page layout instead of being silently truncated. Check both small-icon contrast and genuinely different silhouettes. The CLI cannot certify a logo's identity or subjective visual distinction.

Stop here for preview-only requests. There have been no uploads or Telegram mutations.

## 3. Publish (external side effect)

Only after pack publication is authorized and the actual prepared previews are visually approved:

```sh
python -m topics_icons.cli publish \
  --manifest "$RUN/prepared/manifest.json" --state "$RUN/publication.json" \
  --owner "$OWNER" --bot "$BOT" --prefix MyTopicIconsV1 \
  --confirm-chat "$CHAT" --approve-designs
```

Choose a fresh short prefix. `--approve-designs` is an agent/operator attestation, not an automated vision result. Defaults: 80 icons per set, initial batch up to 50, static custom emoji, `needs_repainting=false`. A pack may contain at most 200 items under this helper's configured limit. Names end in `_by_<bot_username>` and titles are generic. No private topic titles are sent as pack names, sticker keywords or search labels.

The state binds the exact manifest, owner, bot identity, chat and prefix. Re-running the identical command resumes verified uploads/packs without adding unrelated files or replacing existing packs. It checks content by `file_unique_id`, order, dimensions, count and unique custom-emoji IDs. It stops safely on ambiguous pack state. Changing inputs requires a new, explicitly reconciled run; do not hand-edit IDs to conceal a mismatch.

## 4. Apply (external side effect)

```sh
python -m topics_icons.cli apply \
  --manifest "$RUN/prepared/manifest.json" --publication "$RUN/publication.json" \
  --chat "$CHAT" --account "$ACCOUNT" --receipts "$RUN/application.json" \
  --confirm-chat "$CHAT"
```

Each edit is icon-only and carries the expected old icon. Exact readback is required after every write; stale responses receive bounded read-only retries. Re-running after an uncertain write observes the saved pending effect instead of resending it blindly. A conflict or permanently unverifiable effect stops with receipts intact. No automatic rollback is attempted.

## 5. Verify again (Telegram read-only)

```sh
python -m topics_icons.cli verify \
  --manifest "$RUN/prepared/manifest.json" --publication "$RUN/publication.json" \
  --chat "$CHAT" --account "$ACCOUNT" --out "$RUN/verification.json"
```

The final full inventory must have exactly the original topic set, all assigned IDs, distinct nonzero custom-emoji IDs, preserved General and unchanged non-icon fields. Counts and completion claims come from this verified result, not from the number of attempted calls. Never publish these private run files with the reusable skill.
