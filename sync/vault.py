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
