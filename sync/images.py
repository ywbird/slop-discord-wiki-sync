import hashlib
import os
import re
from pathlib import Path


WIKI_IMG_RE = re.compile(r"^!\[\[([^\]|]+)(?:\|[^\]]+)?\]\]$")
MD_IMG_RE = re.compile(r"^!\[([^\]]*)\]\(([^)]+)\)$")


def parse_image_ref(text):
    """Return (ref, description) or None."""
    m = WIKI_IMG_RE.match(text.strip())
    if m:
        return m.group(1), ""
    m = MD_IMG_RE.match(text.strip())
    if m:
        return m.group(2), m.group(1)
    return None


def resolve_source(ref, note_path, vault_root):
    vault = Path(vault_root).resolve()
    note_dir = Path(note_path).resolve().parent
    if ref.startswith(("http://", "https://")):
        return None  # remote: v1 does not download
    if "/" in ref or "\\" in ref:
        cand = (note_dir / ref).resolve() if not ref.startswith("/") else Path(ref)
    else:
        cand = (note_dir / ref).resolve()
    if not cand.exists():
        # vault-wide basename search
        matches = [p for p in vault.rglob(ref) if p.is_file()]
        cand = matches[0] if matches else cand
    try:
        if os.path.commonpath([str(vault), str(cand)]) != str(vault):
            raise ValueError(f"image path escapes vault: {cand}")
    except ValueError:
        raise
    return cand if cand.exists() else None


def file_hash(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return f"sha256:{h.hexdigest()}"
