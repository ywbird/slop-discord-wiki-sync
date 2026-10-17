# AGENTS.md

## Project
Discord wiki→forum sync (Obsidian markdown → Discord forum). Single-file CLI (`sync.py`) with `sync/` modules. Target: Artix Linux, Python 3.14.7. No server/daemon.

## Working directory
Fixed per SPEC: `<repo-root>/` (repo root = runtime cwd).

## Toolchain (uv)
Run everything through uv: `uv run sync.py <cmd> ...`, `uv run python -m tests.test_m1`. Dependencies (`discord.py`, `PyYAML`) are declared in `pyproject.toml` (`uv sync` installs them; `uv.lock` is committed). `[tool.uv] package = false` - the repo is a script, not an installable package.

## M0 first (blocking)
Before coding, verify **O-1–O-7** in §19 against a test forum and record results in SPEC.md:
- O-1: can send `flags` on PATCH? If no, apply only on create.
- O-2: can send content that's just zero-width / `-# (reserved)`? 
- O-3: image slot render order matches doc order (critical).
- O-4–O-6: misc permissions/volume/Pillow need.
- O-7: does `applied_tags` on `POST threads` actually apply tags? What happens with >5 or unknown IDs?

## Deployment defaults (example)
- Forum: `<FORUM_ID>` (guild `<GUILD_ID>`), tag `<TAG_ID>`.
- Set in `config.json` `defaults.forum_id` / `defaults.forum_tags`. Per-note overrides go in `notes`; notes without an override inherit the defaults.
- A separate test forum can be used for M0 checks; threads left there are orphaned in the index.
- Workflow rule: when asked to publish a note without naming a target, upload to **both** the Digital Garden and this Discord forum.

## Implementation order
Current work order: `docs/HANDOFF-2026-10-15-wiki-links.md` (SPEC v1.2 §8.1 conversion wiring + §8.2 wiki link resolution).

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
- **Wire the conversion (§8.1)**: `convert_obsidian_to_discord()` must actually be called — `read -> parse -> convert -> chunk`, text blocks only (codefence/image blocks stay raw). `![[file.png]]` needs a `(?<!!)` lookbehind or image slots break. Tests must go through `_build_slots()`, not the converter directly.
- **Wiki link resolution (§8.2)**: `[[note]]` -> published thread jump link (`[text](https://discord.com/channels/{guild}/{thread})`, default) else garden URL (`dg-publish: true`, slug = `dg-permalink` > stem) else plain display text. Resolution reads `index.json` + vault frontmatter only (0 HTTP, keeps `--dry-run` invariant). `guild_id` from index > `config.notes[...]` > `defaults.guild_id`. Unresolved links = warning, not failure.

## Module layout
`sync.py` is the entry. Modules: `config`, `vault` (read/frontmatter, NFC), `markdown` (Obsidian→Discord §8), `chunker` (§9), `state` (index.json+frontmatter), `discord_api` (sync wrapper over discord.py's HTTPClient + 350ms min), `images` (§14), `apply` (§11), `cli`.

## Config
`config.json` + `secrets.json` (mode 0600). Token may come from `DISCORD_BOT_TOKEN`. `defaults.forum_id` nullable; priority: frontmatter `discord_sync.forum_id` > `config.notes[...]` > defaults. Skip with warning if unknown. `forum_tags` follows the same priority chain (Discord tag IDs, not Obsidian tags).

## Commands
`publish <note>`, `sync <note> [--dry-run|--diff]`, `sync --all`, `status <note>`, `verify <note>`, `adopt <note> --post-id`, `rebalance <note> --yes`, `doctor`, `doctor --tags <forum_id>`. `<note>` is vault-relative path.

## Testing (M1)
U1–U14: boundary splits, codefence atomicity, table behavior, NFD→NFC length, emoji (UTF-16 surrogate pair), wiki/callout conversion, diff (no deletes), buffer policy (K=2→N=2,12→6,20→10,30→10), determinism, tag resolution priority, tag validation (>5 → exit 2, unknown → warn+drop). Keep fixtures in `tests/fixtures/`. M1 must run with no network.

## HTTP details
Base `https://discord.com/api/v10`. `sync/discord_api.py` is a thin **synchronous** wrapper over discord.py's low-level `HTTPClient`/`Route` (not the high-level `Client`/`ForumChannel` API - those cannot send `flags` on thread create or message edit). discord.py owns the aiohttp session, bucket-aware rate limiting, 429/global handling and retries; we add a 350 ms minimum interval before each request. The session is built without the `/users/@me` login call (private attrs, see `_make_client`). Multipart: `HTTPClient.request(route, form=[...], files=[...])` builds the body from `form` only - the file parts must be listed in `form` (`files` is only used for `reset()` on retry). Edit-after-attach: self-heal by `GET` message to recover `attachment_id`, then verify.
