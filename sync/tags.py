MAX_TAGS = 5


class TagError(Exception):
    """Raised for tag misuse that must abort (exit 2)."""


def resolve_target_tags(fm, cfg, note_rel):
    """Desired tag IDs. Priority: frontmatter > config.notes[rel] > defaults."""
    ds = fm.get("discord_sync") or {}
    if "forum_tags" in ds:
        return [str(t) for t in (ds.get("forum_tags") or [])]
    notes = cfg.get("notes", {})
    if note_rel in notes and "forum_tags" in notes[note_rel]:
        return [str(t) for t in (notes[note_rel].get("forum_tags") or [])]
    return [str(t) for t in (cfg.get("defaults", {}).get("forum_tags") or [])]


def check_max(ids):
    if len(ids) > MAX_TAGS:
        raise TagError(f"too many forum_tags: {len(ids)} (max {MAX_TAGS})")


def fetch_available_tags(api, forum_id):
    """Return {id_str: {id, name, moderated}} for a forum channel."""
    ch = api.get_channel(forum_id)
    out = {}
    for t in (ch.get("available_tags") or []):
        out[str(t["id"])] = {
            "id": str(t["id"]),
            "name": t.get("name", ""),
            "moderated": bool(t.get("moderated")),
        }
    return out


def validate(ids, available):
    """Return (known, unknown, warnings). available is {id: meta}."""
    known, unknown = [], []
    for i in ids:
        if i in available:
            known.append(i)
        else:
            unknown.append(i)
    return known, unknown, []


def diff_tags(desired, applied):
    return set(desired) != set(applied)
