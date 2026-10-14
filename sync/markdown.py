import re
import unicodedata


def nfc(s: str) -> str:
    return unicodedata.normalize('NFC', s)


def _strip_frontmatter(text):
    if text.startswith('---'):
        m = re.match(r'^---\s*\n(.*?)\n---\s*(?:\n|$)', text, re.DOTALL)
        if m:
            return text[m.end():]
    return text


def _convert_wiki_links(text, cfg=None):
    cfg = cfg or {}
    mode = cfg.get('wiki_link_mode', 'text')
    def repl_pipe(m):
        name = m.group(1)
        disp = m.group(2)
        return disp if mode == 'text' else f"{disp} (https://notes.meliplug.info/{name})"
    text = re.sub(r'\[\[([^\]|]+)\|([^\]]+)\]\]', repl_pipe, text)
    def repl(m):
        name = m.group(1)
        return name if mode == 'text' else f"{name} (https://notes.meliplug.info/{name})"
    text = re.sub(r'\[\[([^\]]+)\]\]', repl, text)
    return text


def convert_obsidian_to_discord(text, cfg=None):
    if cfg is None:
        cfg = {}
    text = _strip_frontmatter(text)
    text = _convert_wiki_links(text, cfg)
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
