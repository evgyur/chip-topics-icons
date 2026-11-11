# chip-topics-icons

An installable agent skill and portable Python helpers for redesigning all Telegram forum topic icons: unique designs, company/product logos when appropriate, complete previews, resumable custom-emoji publication, icon-only edits and authoritative readback.

This repository contains **no production credentials, private chats, deployed service wiring, real topic inventories or collected brand assets**. The example forum and all example art are synthetic. Nothing starts automatically.

## Install the skill

Inspect the repository and scripts first. Clone it as `chip-topics-icons` into your agent's user skill directory. For a standard Hermes installation:

```sh
git clone https://github.com/evgyur/chip-topics-icons.git "$HOME/.hermes/skills/chip-topics-icons"
```

For a named profile or another agent, use that runtime's documented skill directory instead. Do not overwrite an existing installation. The discovery entrypoint is the root [SKILL.md](SKILL.md); this is not a plugin installer and does not alter your gateway, tools or account sessions.

Example request to your agent:

> Create unique icons for every topic of this forum and apply them. Use company/product logos where applicable. Do not reuse identical icons. Preserve General, exact topic names, pinned/closed/hidden state and all other settings.

Preview-only request:

> Design icons for every topic and show the complete contact sheets and small-size previews. Do not upload files or change Telegram.

## What you need

Python 3.11+ on Linux/macOS, Pillow, and optionally `rsvg-convert` for SVG sources. A Unicode-capable system font is recommended for multilingual titles. The CLI uses a persistent HTTP connection per origin. No paid image API, external model or icon npm package is required.

For real Telegram operations, provide your **existing authenticated forum runtime** implementing [this small adapter contract](references/forum-adapter.md), plus the already-authorized bot and explicit sticker-set owner. This package deliberately does not provision Telethon sessions or ship a private user-account service. The CLI is not a Bot-API-only forum inventory tool. Your account/bot privileges, Premium/custom-emoji eligibility and actual adapter support must be verified in your own forum; a denied effect is a blocker, not permission to switch accounts or bypass a gate.

## Offline quick start

Run from the repository root. Use a private directory outside the checkout for every generated file:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
RUN="$(mktemp -d "${TMPDIR:-$HOME/.cache}/topic-icons-demo.XXXXXX")"
chmod 700 "$RUN"
python -m topics_icons.cli prepare \
  --inventory examples/inventory.json \
  --design examples/design.json \
  --assets examples/assets \
  --out "$RUN/prepared"
python -m topics_icons.cli check --manifest "$RUN/prepared/manifest.json"
```

The SVG example needs `rsvg-convert` (for example, the `librsvg2-bin` package on Debian/Ubuntu). Alternatively supply PNG/WebP assets; the tests use locally generated PNGs and do not need librsvg. The demo creates three distinct custom-emoji files, a manifest, a fully labelled contact sheet and a 22px list mockup including General's system-icon placeholder. Open the images with your agent's vision tool. These are mockups, not screenshots of Telegram.

Nothing above uploads files, contacts Telegram, reads credentials or changes a forum. Remove only your own demo directory when no longer needed; keep actual run receipts privately.

## Real run

Follow [CLI configuration and commands](references/cli.md). The stages are deliberately separate:

1. Read every topic page, including archived/closed topics and General.
2. Establish topic meanings without leaking private messages to image services.
3. Source approved logos or create original thematic artwork; record provenance and rights.
4. Prepare all icons, then visually inspect every full-size and 22px preview.
5. Explicitly publish the custom-emoji packs through the incumbent bot.
6. Explicitly apply only topic-icon fields with expected-prior-value checks.
7. Re-read the complete forum and prove coverage, unique IDs, preserved General and unchanged non-icon fields.

The helper validates distinct design identifiers and decoded image pixels, but an agent/human must still reject same-silhouette color variants and confirm that a purported company mark is correct. It does not autonomously infer brand identity or generate finished artwork from topic titles. The skill supplies that design workflow rather than pretending that a filename/hash check is visual QA.

Emoji packs can be shared publicly. Do not place private topic names, chat identities, customer art or confidential marks in pack metadata. The helper uses generic pack titles and no topic keywords. Trademark and source-license compliance remain the operator's responsibility; the MIT license here covers the code and original synthetic examples, not logos obtained later.

## Verification

```sh
python scripts/validate_skill.py
python -m unittest discover -s tests -v
python -m topics_icons.cli --help
```

The tests exercise real Pillow rendering and a real local HTTP fixture through the production transport/publication/application code, including pagination, static multipart uploads, stale readbacks, resumability, conflicts, exact scope, privacy-safe errors and a forged-success negative case. They shut down their listener and verify closure. Fixtures never use a real Telegram token or account.

These tests are not a new live Telegram rollout, a visual-quality benchmark or proof that your account can set arbitrary custom emoji. No GitHub Actions are configured.

## Recovery and boundaries

See [recovery](references/recovery.md) and [public export boundaries](references/export-boundaries.md). Uploading a pack is not installing topic icons; a local test pass is not Telegram readback. Ambiguous writes stop rather than being blindly resent. No automatic rollback, permission changes, session creation or service restart is performed.

License: [MIT](LICENSE).
