import hashlib
import unicodedata


LIMIT_DEFAULT = 1900


def nfc(s: str) -> str:
    return unicodedata.normalize('NFC', s)


def utf16_len(s):
    return len(s.encode('utf-16-le')) // 2


class SlotPlan:
    def __init__(self, i, role, content='', **kw):
        self.i = i
        self.role = role
        self.content = content
        for k, v in kw.items():
            setattr(self, k, v)


def slot_hash_content(txt):
    h = hashlib.sha256()
    h.update(b'text\n')
    h.update(nfc(txt).encode('utf-8'))
    return f"sha256:{h.hexdigest()[:32]}"


def slot_hash_image(src_hash, description=''):
    h = hashlib.sha256()
    h.update(b'image\n')
    h.update((src_hash or '').encode('utf-8'))
    h.update(b'\n')
    h.update((description or '').encode('utf-8'))
    return f"sha256:{h.hexdigest()[:32]}"


def slot_hash_buffer():
    h = hashlib.sha256()
    h.update(b'buffer')
    return f"sha256:{h.hexdigest()[:32]}"


def slot_hash_snapshot(payload_hash):
    h = hashlib.sha256()
    h.update(b'snapshot\n')
    h.update((payload_hash or '').encode('utf-8'))
    return f"sha256:{h.hexdigest()[:32]}"


def plan_content_hash(plan_slots):
    # plan_slots are SlotPlan-like
    parts = []
    for s in plan_slots:
        if s.role == 'content':
            parts.append(slot_hash_content(s.content))
        elif s.role == 'image':
            parts.append(slot_hash_image(getattr(s, 'src_hash', ''), getattr(s, 'description', '')))
        elif s.role == 'buffer':
            parts.append(slot_hash_buffer())
        elif s.role == 'snapshot':
            parts.append(slot_hash_snapshot(getattr(s, 'payload_hash', '')))
        else:
            parts.append(slot_hash_content(s.content))
    h = hashlib.sha256()
    h.update('\n'.join(parts).encode('utf-8'))
    return f"sha256:{h.hexdigest()}"


def build_plan(blocks, cfg=None):
    if cfg is None:
        cfg = {}
    limit = cfg.get('limit', LIMIT_DEFAULT)
    plan = []
    buf_texts = []

    def flush():
        if not buf_texts:
            return
        txt = '\n\n'.join(t for t in buf_texts if t)
        if not txt:
            buf_texts.clear()
            return
        txt = nfc(txt)
        plan.append(SlotPlan(len(plan), 'content', content=txt))
        buf_texts.clear()

    for b in blocks:
        t = b['text']
        btype = b['type']
        if btype == 'image':
            flush()
            plan.append(SlotPlan(len(plan), 'image', content='-# (image)', image_text=t))
            continue
        # text/codefence
        cur = '\n\n'.join(buf_texts + [t]) if buf_texts else t
        if utf16_len(cur) <= limit:
            buf_texts.append(t)
        else:
            flush()
            if utf16_len(t) <= limit:
                buf_texts.append(t)
            else:
                s = nfc(t)
                while utf16_len(s) > limit:
                    cut = int(limit * 0.9)
                    plan.append(SlotPlan(len(plan), 'content', content=s[:cut]))
                    s = s[cut:]
                if s:
                    plan.append(SlotPlan(len(plan), 'content', content=s))
    flush()
    return plan


def diff(plan, state_slots):
    """plan: list of SlotPlan; state_slots: list of dicts with role/id/hash.
    Returns list of ops dicts."""
    ops = []
    for i, s in enumerate(plan):
        if s.role == 'content':
            h = slot_hash_content(s.content)
        elif s.role == 'image':
            h = slot_hash_image(getattr(s, 'src_hash', ''), getattr(s, 'description', ''))
        elif s.role == 'buffer':
            h = slot_hash_buffer()
        elif s.role == 'snapshot':
            h = slot_hash_snapshot(getattr(s, 'payload_hash', ''))
        else:
            h = slot_hash_content(s.content)
        s.hash = h
        old = state_slots[i] if i < len(state_slots) else None
        if old and old.get('role') == s.role and old.get('hash') == h:
            continue
        if old:
            ops.append({'kind': 'edit', 'i': i, 'message_id': old.get('id'),
                        'from': old.get('role'), 'to': s.role, 'target': s})
        else:
            ops.append({'kind': 'create', 'i': i, 'target_role': s.role, 'target': s})
    for j in range(len(plan), len(state_slots)):
        old = state_slots[j]
        if old.get('role') == 'buffer':
            continue
        ops.append({'kind': 'revert_to_buffer', 'i': j, 'message_id': old.get('id')})
    return ops
