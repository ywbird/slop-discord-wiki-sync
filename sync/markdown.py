import re
import unicodedata
from pathlib import Path


def nfc(s: str) -> str:
    return unicodedata.normalize('NFC', s)


def _strip_frontmatter(text):
    if text.startswith('---'):
        m = re.match(r'^---\s*\n(.*?)\n---\s*(?:\n|$)', text, re.DOTALL)
        if m:
            return text[m.end():]
    return text


WIKI_PIPE = re.compile(r'(?<!!)\[\[([^\]|]+)\|([^\]]+)\]\]')
WIKI_PLAIN = re.compile(r'(?<!!)\[\[([^\]|]+)\]\]')


def new_wiki_stats():
    return {"discord": 0, "garden": 0, "text": 0, "unresolved": [], "warnings": []}


def default_link_ctx(cfg=None):
    cfg = cfg or {}
    return {
        "mode": cfg.get("wiki_link_mode", "discord"),
        "style": cfg.get("wiki_link_style", "url"),
        "fallback": cfg.get("wiki_link_fallback", "garden"),
        "garden_base_url": cfg.get("garden_base_url", "https://example.com"),
        "index_notes": {},
        "stem_map": {},
        "notes_cfg": {},
        "defaults_guild_id": None,
        "read_frontmatter": None,
    }


def garden_slug(fm, rel):
    perm = (fm or {}).get("dg-permalink")
    if perm:
        return str(perm).strip("/")
    stem = Path(rel).stem.lower().replace(" ", "-")
    if stem and all(ord(c) < 128 for c in stem):
        return stem
    return None


def _warn(stats, msg):
    seen = stats.setdefault("_seen", set())
    if msg not in seen:
        seen.add(msg)
        stats["warnings"].append(msg)


def _load(rel, ctx):
    reader = ctx.get("read_frontmatter")
    fm = reader(rel) if reader else {}
    notes = ctx.get("index_notes") or {}
    return {"rel": rel, "entry": notes.get(rel), "fm": fm or {}}


def find_target(target, ctx):
    """Return dict(rel, entry, fm) | "ambiguous" | None. Deterministic."""
    notes = ctx.get("index_notes") or {}
    hits = []
    for key in (target, target + ".md"):
        if key in notes and key not in hits:
            hits.append(key)
    if len(hits) == 1:
        return _load(hits[0], ctx)
    if len(hits) > 1:
        return "ambiguous"
    matches = (ctx.get("stem_map") or {}).get(target, [])
    if len(matches) == 1:
        return _load(matches[0], ctx)
    if len(matches) > 1:
        return "ambiguous"
    return None


def _guild_for(rel, entry, ctx):
    if entry and entry.get("guild_id"):
        return entry["guild_id"]
    g = (ctx.get("notes_cfg") or {}).get(rel, {}).get("guild_id")
    if g:
        return g
    return ctx.get("defaults_guild_id")


def _garden_render(target, disp, ctx, stats, found, require_publish):
    if isinstance(found, dict):
        fm = found["fm"]
        if (not require_publish) or fm.get("dg-publish") in (True, "true"):
            slug = garden_slug(fm, found["rel"])
            if slug:
                stats["garden"] += 1
                return f"[{disp}]({ctx['garden_base_url']}/{slug})"
    stats["text"] += 1
    stats["unresolved"].append(target)
    return disp


def _discord_render(target, disp, ctx, stats):
    found = find_target(target, ctx)
    if found == "ambiguous":
        _warn(stats, f"wiki link ambiguous: {target}")
        stats["text"] += 1
        stats["unresolved"].append(target)
        return disp
    entry = found["entry"] if isinstance(found, dict) else None
    if entry and entry.get("thread_id"):
        tid = entry["thread_id"]
        if ctx.get("style") == "mention":
            stats["discord"] += 1
            return f"<#{tid}>"
        guild = _guild_for(found["rel"], entry, ctx)
        if guild:
            stats["discord"] += 1
            return f"[{disp}](https://discord.com/channels/{guild}/{tid})"
        _warn(stats, f"wiki link: no guild_id for {target}; falling back")
    if ctx.get("fallback", "garden") == "garden":
        return _garden_render(target, disp, ctx, stats, found, require_publish=True)
    stats["text"] += 1
    stats["unresolved"].append(target)
    return disp


def _render(target, disp, mode, ctx, stats):
    if mode == "text":
        stats["text"] += 1
        return disp
    if mode in ("garden", "url"):
        return _garden_render(target, disp, ctx, stats, find_target(target, ctx), require_publish=False)
    return _discord_render(target, disp, ctx, stats)


def _convert_wiki_links(text, mode, ctx, stats):
    def sub_pipe(m):
        target = m.group(1).strip().split("#", 1)[0].strip()
        return _render(target, m.group(2), mode, ctx, stats)

    def sub(m):
        target = m.group(1).strip().split("#", 1)[0].strip()
        return _render(target, target, mode, ctx, stats)

    text = WIKI_PIPE.sub(sub_pipe, text)
    text = WIKI_PLAIN.sub(sub, text)
    return text


def convert_obsidian_to_discord(text, cfg=None, link_ctx=None, stats=None):
    """Convert one block's worth of Obsidian markdown to Discord markdown."""
    if cfg is None:
        cfg = {}
    if stats is None:
        stats = new_wiki_stats()
    if link_ctx is None:
        link_ctx = default_link_ctx(cfg)
    mode = link_ctx.get("mode", "discord")
    text = _strip_frontmatter(text)
    text = _convert_wiki_links(text, mode, link_ctx, stats)
    cmode = cfg.get('callout_mode', 'quote')
    if cmode == 'quote':
        text = re.sub(r'^> \[![^\]]+\]\s+(.*)$', r'> **\1**', text, flags=re.M)
    else:
        text = re.sub(r'^> \[![^\]]+\].*\n?', '', text, flags=re.M)
    text = re.sub(r'==([^=]+)==', r'\1', text)
    text = re.sub(r'\$\$.*?\$\$', '', text, flags=re.S)
    text = re.sub(r'\$[^$]+\$', '', text)
    text = re.sub(r'\[\^[^\]]+\]', '', text)
    return nfc(text)


def convert_blocks(blocks, cfg=None, link_ctx=None):
    """Convert text blocks only; codefence/image blocks stay raw (§8.1).

    Empty results (e.g. a paragraph that was only a formula) are dropped.
    Returns (blocks, stats).
    """
    stats = new_wiki_stats()
    out = []
    for b in blocks:
        if b["type"] == "text":
            converted = convert_obsidian_to_discord(b["text"], cfg, link_ctx, stats)
            if converted.strip():
                out.append({"type": "text", "text": converted})
        else:
            out.append(b)
    return out, stats


def parse_blocks(text):
    text = nfc(_strip_frontmatter(text))
    blocks = []
    i = 0
    n = len(text)
    while i < n:
        # code fence
        if i + 2 < n and text[i:i+3] == '```':
            j = text.find('```', i + 3)
            if j == -1:
                # unclosed
                blocks.append({'type': 'codefence', 'text': text[i:]})
                break
            # find end of line after closing
            k = text.find('\n', j + 3)
            if k == -1:
                end = len(text)
            else:
                end = k + 1
            blocks.append({'type': 'codefence', 'text': text[i:end]})
            i = end
            continue
        # image ![ or ![[
        if text[i:i+2] == '![':
            # find end of line or next block boundary? simple line-based
            endline = text.find('\n', i)
            if endline == -1:
                blocks.append({'type': 'image', 'text': text[i:]})
                break
            line = text[i:endline]
            blocks.append({'type': 'image', 'text': line})
            i = endline + 1
            continue
        # blank line skip to next
        if text[i] in '\n\r':
            i += 1
            continue
        # text block until blank line or fence/image
        start = i
        end = i
        while end < n:
            if end + 2 < n and text[end:end+3] == '```':
                break
            if end + 1 < n and text[end:end+2] == '![':
                break
            if text[end] in '\n\r':
                # peek blank
                j = end
                while j < n and text[j] in '\n\r':
                    j += 1
                if j > end + 1:
                    end = j
                    break
            end += 1
        blk = text[start:end]
        blk = blk.strip('\n\r\t ')
        if blk:
            blocks.append({'type': 'text', 'text': blk})
        i = end
    return blocks
