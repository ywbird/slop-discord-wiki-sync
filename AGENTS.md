# AGENTS.md

## Project
Discord wiki→forum sync (Obsidian markdown → Discord forum). Single-file CLI (`sync.py`) with `sync/` modules. Target: Artix Linux, Python 3.14.7. No server/daemon.

## Working directory
Fixed per SPEC: `/home/ywbird/dev/slop-discord-wiki-sync/` (repo root = runtime cwd).

## M0 first (blocking)
Before coding, verify **O-1–O-7** in §19 against a test forum and record results in SPEC.md:
- O-1: can send `flags` on PATCH? If no, apply only on create.
- O-2: can send content that's just zero-width / `-# (reserved)`? 
- O-3: image slot render order matches doc order (critical).
- O-4–O-6: misc permissions/volume/Pillow need.
- O-7: does `applied_tags` on `POST threads` actually apply tags? What happens with >5 or unknown IDs?

## Production defaults (2026-10-15)
- Forum: `1451142756495003658` (guild `761435154588631060` = Cognipotato, forum `도`), tag `1559989396378353684` (`bot`).
- Set in `config.json` `defaults.forum_id` / `defaults.forum_tags`. No per-note overrides (`notes` is empty) - the vault notes inherit the defaults.
- Old test forum `1559982578881011752` (guild `906454588276756490` = Nycko Server, `ㅅㄷㄴ`, tag `1559988468501844069` = `ㅅㄴ`) is no longer used. Its two leftover threads (피라네시, 픽션들) are orphaned in the index.
- 2026-10-15: all 12 published vault notes posted to the production forum via `sync.py publish` (12/12, 0 failures), rerun via `sync --all` = all no-op.
- Workflow rule from 도이: when asked to publish a note without naming a target, upload to **both** the Digital Garden and this Discord forum.

## Implementation order
M1 (pure, no network) → M2 (publish) → M3 (sync) → M4 (images) → M5 (ops). `--dry-run` must make **0 HTTP calls** (enforce via HTTP layer counter).

## Core rules (easy to miss)
- **Length**: UTF-16 code units `len(s.encode("utf-16-le"))//2`, not `len(s)`. Measure after NFC + conversion.
- **NFC mandatory**: normalize on read and after conversion. NFD Korean inflates char count.
- **Attachments on PATCH**: C-4 — omitting `attachments` deletes existing attachments. Always send full `attachments` array (or `[]`).
- **Slots immutable order**: never delete/reorder; to shrink, revert trailing slots to `buffer` role (`-# (reserved)`). Buffers consumed left-to-right.
- **Content slots**: must have no attachments. Image slots own attachments. Empty content is not allowed (use placeholder).
- **No-op by hash**: skip edits if role+hash unchanged. Top-level `content_hash` gates sync.
- **Forum tags**: on create, send `applied_tags` (IDs from frontmatter `discord_sync.forum_tags` > `config.notes[...]` > `defaults.forum_tags`). Validate against the forum's `available_tags`, max 5, drop unknown with a warning. See SPEC §13.7.
- **Thread title sync (§13.8)**: desired = frontmatter `title` else filename stem, truncated to 100 code points. Applied = `index.json` `thread_name` (never frontmatter). Merge `name` into the §13.2 state PATCH only when it differs; store the PATCH/GET response `name` (not the request value). No-op only when slots + tags + title are all unchanged (0 HTTP).
- **Atomic writes**: write temp + `os.replace` for `index.json`. Keep YAML frontmatter intact (2-space indent), preserve other keys.

## Module layout
`sync.py` is the entry. Modules: `config`, `vault` (read/frontmatter, NFC), `markdown` (Obsidian→Discord §8), `chunker` (§9), `state` (index.json+frontmatter), `discord_api` (serialized queue + 350ms min + 429/retry), `images` (§14), `apply` (§11), `cli`.

## Config
`config.json` + `secrets.json` (mode 0600). Token may come from `DISCORD_BOT_TOKEN`. `defaults.forum_id` nullable; priority: frontmatter `discord_sync.forum_id` > `config.notes[...]` > defaults. Skip with warning if unknown. `forum_tags` follows the same priority chain (Discord tag IDs, not Obsidian tags).

## Commands
`publish <note>`, `sync <note> [--dry-run|--diff]`, `sync --all`, `status <note>`, `verify <note>`, `adopt <note> --post-id`, `rebalance <note> --yes`, `doctor`, `doctor --tags <forum_id>`. `<note>` is vault-relative path.

## Testing (M1)
U1–U14: boundary splits, codefence atomicity, table behavior, NFD→NFC length, emoji (UTF-16 surrogate pair), wiki/callout conversion, diff (no deletes), buffer policy (K=2→N=2,12→6,20→10,30→10), determinism, tag resolution priority, tag validation (>5 → exit 2, unknown → warn+drop). Keep fixtures in `tests/fixtures/`. M1 must run with no network.

## HTTP details
Base `https://discord.com/api/v10`. Single serialized queue (no parallel). Multipart for image uploads/edits with file parts. On 429 respect `retry_after`/`X-RateLimit-*`; global blocks queue. Edit-after-attach: self-heal by `GET` message to recover `attachment_id`, then verify.
