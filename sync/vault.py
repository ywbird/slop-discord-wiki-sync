import os
import unicodedata
from pathlib import Path


def nfc(s: str) -> str:
    return unicodedata.normalize('NFC', s)


def read_note(path, vault_root=None):
    p = Path(path)
    if vault_root:
        v = Path(vault_root).resolve()
        if not p.is_absolute():
            p = (v / p).resolve()
        if os.path.commonpath([v, p]) != str(v):
            raise ValueError(f"Path escapes vault: {p}")
    else:
        p = p.resolve()
    if not p.exists():
        raise FileNotFoundError(f"Note not found: {p}")
    text = p.read_text(encoding='utf-8')
    text = nfc(text)
    return text, p


def read_frontmatter_file(path):
    """Read only the YAML frontmatter of a note; never touches the body (§8.2)."""
    p = Path(path)
    if not p.is_file():
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            if f.readline().strip() != "---":
                return {}
            buf = []
            for line in f:
                if line.rstrip("\n").strip() == "---":
                    break
                buf.append(line)
            else:
                return {}
    except OSError:
        return {}
    try:
        import yaml
    except ImportError:
        return {}
    return yaml.safe_load("".join(buf)) or {}


def stem_map(vault_root):
    """Map filename stem -> [relative paths], deterministic order. (§8.2 step 2)"""
    out = {}
    root = Path(vault_root)
    if not root.is_dir():
        return out
    for p in sorted(root.rglob("*.md")):
        out.setdefault(p.stem, []).append(str(p.relative_to(root)))
    return out
