# slop-discord-wiki-sync

[English](README.md) | [한국어](README.ko.md)

Publish an Obsidian vault note as a **Discord forum thread**, then keep it in sync by **editing messages only** - so comments and deep links survive every update.

- No server, no daemon. A single script (`sync.py`) plus `sync/` modules.
- One-way sync: vault -> Discord. Edits made on the Discord side are overwritten on the next sync.
- `--dry-run` guarantees **zero HTTP calls** (enforced by a counter in the HTTP layer).

---

## Why it works this way

Discord forum messages **cannot be inserted in the middle**. New messages always land at the end of the thread. But people comment on posts. So if "the note changed, post the body again" were done as a new message, the updated document would be pushed below the comments and become unreadable.

The fix:

1. **Create every body slot up front, at publish time.** Later updates are applied by editing those slots. Edits never change order, so the top of the document stays put even with comments present.
2. **Reserve spare slots (buffers) at the tail.** When the document grows and needs more slots, creating new messages would push them below the comments.

---

## Features

- **Slot model**: one Discord message = one slot. Roles: `content` (body text), `image` (exactly one attachment), `buffer` (spare, `-# (reserved)`), `snapshot` (the original `.md` as an attachment).
- **Adaptive buffers**: reserve N spare slots proportional to the slot count K (`ratio` 0.5, `min` 2, `max` 10). Shrinking never deletes: trailing slots revert to the `buffer` role, so order is immutable.
- **Hash-based no-op**: if role + slot hash are unchanged, nothing is edited. If the top-level `content_hash` matches, the sync is skipped entirely (zero HTTP).
- **Exact length accounting**: measured in UTF-16 code units (`len(s.encode("utf-16-le"))//2`). NFC normalization is mandatory (NFD Korean inflates the character count).
- **Attachment preservation**: omitting `attachments` on a message edit deletes existing attachments, so the full `attachments` array is always sent.
- **Obsidian -> Discord conversion**: wiki links, callouts, highlights, math, and footnotes are converted to Discord syntax (code fences and image blocks stay raw).
- **Wiki link resolution**: `[[note]]` becomes a jump link to the published Discord thread (falling back to a garden URL, then plain display text). Resolution reads only `index.json` + vault frontmatter, so it makes zero HTTP calls and never recurses.
- **Forum tags / thread title sync**: tags are applied by frontmatter priority, and title changes are merged into the same PATCH.
- **Rate limit compliance**: requests go through discord.py's HTTP layer (bucket-aware rate limiting, 429/global handling, retries) with an additional minimum interval between requests (350 ms by default).
- **Atomic state writes**: `index.json` is written to a temp file and `os.replace`d.

---

## Requirements

- Python 3.11+ (developed and verified on Artix Linux, Python 3.14.7)
- [uv](https://docs.astral.sh/uv/) (recommended) or a plain `python3`
- Dependencies: `discord.py` and `PyYAML` (`uv sync` installs them)
- A Discord bot token. The bot is assumed to hold the **Administrator** role in the target guild (channel permission checks are skipped; unarchive/unlock/tags/rename all work).

---

## Install

```bash
git clone <repo-url> slop-discord-wiki-sync
cd slop-discord-wiki-sync
uv sync                              # creates .venv and installs dependencies
cp config.example.json config.json   # fill in your values
```

Without uv, install the dependency yourself and call `python3 sync.py` instead of `uv run sync.py`:

```bash
python3 -m pip install discord.py pyyaml
```

Provide the token one of two ways:

```bash
# 1) secrets.json (recommended, mode 0600)
printf '{"bot_token": "YOUR_BOT_TOKEN"}' > secrets.json && chmod 600 secrets.json

# 2) environment variable
export DISCORD_BOT_TOKEN=YOUR_BOT_TOKEN
```

---

## Configuration

### `config.json`

| Key | Meaning |
|---|---|
| `vault_root` | Absolute path to the Obsidian vault |
| `index_path` | Sidecar state file (default `index.json`) |
| `defaults.forum_id` | Target forum channel ID. If `null`, each note must specify one |
| `defaults.forum_tags` | Discord tag IDs to apply (max 5) |
| `defaults.guild_id` | Guild ID used to build jump links |
| `defaults.limit` | Max length of a single message (default 1900) |
| `defaults.buffer_policy` | `{mode, min, max, ratio}` |
| `defaults.snapshot_attachment` | Whether to create the original-`.md` attachment slot |
| `defaults.callout_mode` | `quote` (callouts become quotes) / `strip` (remove) |
| `defaults.wiki_link_mode` | `discord` (default) / `garden` / `text` |
| `defaults.wiki_link_style` | `url` (default) / `mention` |
| `defaults.wiki_link_fallback` | `garden` (default) / `text` |
| `defaults.garden_base_url` | Garden URL prefix |
| `defaults.image` | `{max_bytes, max_width, format, one_image_per_slot}` |
| `defaults.rate_limit` | `{min_interval_ms, max_retries}` |
| `notes` | Per-note overrides (path -> settings). Empty means everything inherits `defaults` |

Priority: frontmatter `discord_sync.*` > `config.notes[path]` > `defaults`.

### Note frontmatter (opt-in)

```yaml
---
title: UBL
discord_sync:
  enabled: true          # without true, the note is not synced
  forum_id: "1234567890" # omit to inherit defaults
  forum_tags: ["1111111111111111111"]
  post_id: "9876543210"  # written by the script after publishing
  content_hash: "sha256:..."
  synced_at: "2026-10-14T21:00:00+09:00"
---
```

`sync --all` walks the vault recursively and processes only notes with `discord_sync.enabled: true`.

---

## Usage

```bash
uv run sync.py publish <note> [--force]              # publish a new thread
uv run sync.py sync <note> [--dry-run] [--diff]      # apply changes
uv run sync.py sync --all [--dry-run]                # every enabled note
uv run sync.py status <note>                         # print plan vs. state diff
uv run sync.py verify <note>                         # GET the live thread and compare with state
uv run sync.py adopt <note> --post-id <id> [--starter-id <id>]   # re-adopt an existing thread
uv run sync.py rebalance <note> --yes                # repost as a new thread (loses comments/deep links)
uv run sync.py doctor                                # check token/vault/modules
uv run sync.py doctor --tags <forum_id>              # print the forum's available_tags
```

Common options: `--config <path>` (default `./config.json`), `--json` (machine-readable output), `--verbose`.

`<note>` is a vault-relative path (absolute paths are allowed but must stay inside the vault).

Examples:

```bash
# Preview changes only (no network calls)
uv run sync.py sync "UBL.md" --dry-run --diff

# Apply
uv run sync.py sync "UBL.md"

# Sync the whole vault
uv run sync.py sync --all
```

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Success (including no changes) |
| 1 | Usage / argument error |
| 2 | Config, token, or forum not specified |
| 3 | Vault / note read error |
| 4 | Partial failure (some slots failed). Re-running continues from there |
| 5 | State mismatch (manual action needed). Points to `doctor`/`adopt` |
| 6 | Buffers exhausted + growth exceeded -> `rebalance` recommended |

---

## How it works

```
read -> parse (block split) -> convert (Obsidian->Discord) -> chunk (slot plan)
     -> diff (plan vs index.json) -> apply (create/edit) -> persist (index.json + frontmatter)
```

- **Invariant**: slot order is never changed. Shrinking is not deletion either - trailing slots revert to the `buffer` role. Buffers are consumed left to right.
- **Content slots must have no attachments; image slots own exactly one.** Empty content is not allowed (a placeholder is used).
- **`synced_at`** = "the time the Discord state was actually written". It is not updated on a no-op (which keeps the zero-HTTP invariant).
- **`index.json` is the per-thread source of truth.** If it disagrees with frontmatter, `index.json` wins.

---

## Markdown conversion (Obsidian -> Discord)

| Input | Output |
|---|---|
| `[[note]]`, `[[note\|display]]` | Mode-dependent (default: jump link to the published thread) |
| `![[file.png]]`, `![alt](path.png)` | Image slot (alt becomes `description`) |
| `> [!note] Title` + body | `> **Title**` + `> body` |
| `$math$`, `==highlight==`, `^[footnote]`, `[^1]` | Markers stripped, demoted to text |
| Code fences | Kept raw (atomic block) |
| `#`-`######` headings | Kept as-is (Discord renders only `#`/`##`/`###`) |

Conversion applies to **text blocks only**. `![[...]]` is an image embed, so it is excluded from wiki-link substitution (the regex requires the negative lookbehind `(?<!!)`).

---

## Tests

```bash
uv run python -m tests.test_m1     # 24/24 passed (no pytest needed)
```

The M1 unit tests run without network access. They cover boundary splits, code fence atomicity, NFD->NFC length, emoji (surrogate pairs), wiki-link/callout conversion, buffer policy, determinism, tag priority and validation, and the zero-HTTP `--dry-run` invariant.

---

## Layout

```
slop-discord-wiki-sync/
  sync.py              # entry point
  sync/
    config.py          # config load/validate
    vault.py           # vault note reading, frontmatter, NFC
    markdown.py        # block parser + Obsidian->Discord conversion
    chunker.py         # slot plan generation
    state.py           # index.json + frontmatter state
    discord_api.py     # sync wrapper over discord.py's HTTPClient
    images.py          # image resolution/normalization/hashing
    apply.py           # plan -> Discord
    tags.py            # forum tag resolution/validation
    cli.py             # argument parsing, output
  tests/
    test_m1.py         # M1 unit tests
    fixtures/          # input notes + expected plans
  docs/
    HANDOFF-2026-10-15-wiki-links.md
  SPEC.md              # single source of design (full spec)
  AGENTS.md            # working rules for coding agents
  config.example.json  # example config
  pyproject.toml       # project metadata + dependencies (uv)
  uv.lock              # locked dependency versions
  LICENSE              # VibeCoded AI-Slop License v1.0
```

---

## Docs

- **`SPEC.md`** - the single source of design: data model, conversion rules, chunking, sync algorithm, REST contract, CLI spec, test plan, milestones.
- **`AGENTS.md`** - working rules for coding agents (a collection of easy-to-miss constraints).

---

## License

[VibeCoded AI-Slop License v1.0](LICENSE) - Copyright (c) 2026 ywbird

Do whatever you want with it. No permission, attribution, or publishing of modifications required.
