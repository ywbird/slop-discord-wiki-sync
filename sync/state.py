import json
import os
import re
import tempfile
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None

FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*(?:\n|$)", re.DOTALL)


def load_index(index_path):
    p = Path(index_path)
    if not p.exists():
        return {"schema_version": 1, "notes": {}}
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def save_index(index_path, data):
    atomic_write_json(index_path, data)


def atomic_write_json(path, data):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=p.name + ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, str(p))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_frontmatter(text):
    """Return (frontmatter_dict, body)."""
    m = FM_RE.match(text)
    if not m:
        return {}, text
    if yaml is None:
        raise RuntimeError("PyYAML required for frontmatter")
    fm = yaml.safe_load(m.group(1)) or {}
    return fm, text[m.end():]


def _dump_yaml_block(fm):
    return yaml.safe_dump(fm, allow_unicode=True, default_flow_style=False, sort_keys=False)


def write_frontmatter(path, fm, body):
    block = _dump_yaml_block(fm)
    content = f"---\n{block}---\n{body}"
    p = Path(path)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=p.name + ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, str(p))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def update_note_frontmatter(path, sync_block, body):
    """Merge sync_block into existing frontmatter keys, preserving others."""
    text = Path(path).read_text(encoding="utf-8")
    fm, _ = read_frontmatter(text)
    fm = fm or {}
    fm["discord_sync"] = sync_block
    write_frontmatter(path, fm, body)
