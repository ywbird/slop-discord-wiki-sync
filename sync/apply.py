import math

from . import chunker, images

BUFFER_CONTENT = "-# (reserved)"
IMAGE_CONTENT = "-# (image)"

FLAG_SUPPRESS_EMBEDS = 1 << 2
FLAG_SUPPRESS_NOTIFICATIONS = 1 << 12


def buffer_count(K, ratio=0.5, minv=2, maxv=10):
    N = math.ceil(K * ratio)
    if N < minv:
        N = minv
    if N > maxv:
        N = maxv
    return N


def enrich_image_slots(plan, note_path, vault_root):
    """Attach src/src_hash/description to image SlotPlans; downgrade missing to text."""
    out = []
    for s in plan:
        if s.role != "image":
            out.append(s)
            continue
        parsed = images.parse_image_ref(s.image_text)
        if not parsed:
            out.append(s)
            continue
        ref, desc = parsed
        src = images.resolve_source(ref, note_path, vault_root)
        if src is None:
            # downgrade to link text
            s.role = "content"
            s.content = ref
            out.append(s)
            continue
        s.src = str(src)
        s.src_hash = images.file_hash(src)
        s.description = desc
        s.content = IMAGE_CONTENT
        s.filename = src.name
        out.append(s)
    return out


def slot_hash(s):
    if s.role == "content":
        return chunker.slot_hash_content(s.content)
    if s.role == "image":
        return chunker.slot_hash_image(getattr(s, "src_hash", ""), getattr(s, "description", ""))
    if s.role == "buffer":
        return chunker.slot_hash_buffer()
    if s.role == "snapshot":
        return chunker.slot_hash_snapshot(getattr(s, "payload_hash", ""))
    return chunker.slot_hash_content(s.content)


def plan_content_hash(slots):
    parts = [slot_hash(s) for s in slots]
    return chunker.plan_content_hash(slots)


def buffer_plans(existing_buffers, n_needed):
    return [chunker.SlotPlan(i, "buffer", content=BUFFER_CONTENT) for i in range(n_needed)]


def apply_ops(api, thread_id, ops, state_slots):
    """Execute ops sequentially. Returns (new_state_slots, failures)."""
    failures = []
    for op in ops:
        try:
            if op["kind"] == "edit":
                _apply_edit(api, thread_id, op, state_slots)
            elif op["kind"] == "create":
                _apply_create(api, thread_id, op, state_slots)
            elif op["kind"] == "revert_to_buffer":
                _apply_revert(api, thread_id, op, state_slots)
        except Exception as e:  # noqa: BLE001
            failures.append({"op": op, "error": str(e)})
    return state_slots, failures


def _apply_edit(api, thread_id, op, state_slots):
    t = op["target"]
    i = op["i"]
    mid = op["message_id"]
    if t.role == "image":
        old = state_slots[i]
        att = _recover_attachment(api, thread_id, mid, old)
        files = [(t.filename, open(t.src, "rb").read())] if t.src_hash != old.get("src_hash") else None
        attachments = [] if files else att
        if files:
            attachments = [{"id": 0, "filename": t.filename,
                            "description": getattr(t, "description", "")}]
        elif attachments:
            attachments[0]["description"] = getattr(t, "description", "")
        res = api.edit_message(thread_id, mid, IMAGE_CONTENT, attachments, files=files)
        _verify_or_heal(api, thread_id, mid, res, t, old)
        state_slots[i] = _slot_state(i, t, res)
    elif t.role == "snapshot":
        old = state_slots[i]
        files = None
        attachments = []
        if getattr(t, "payload_hash", None) != old.get("payload_hash"):
            files = [(t.filename, open(t.src, "rb").read())]
            attachments = [{"id": 0, "filename": t.filename}]
        else:
            att = _recover_attachment(api, thread_id, mid, old)
            attachments = att
        res = api.edit_message(thread_id, mid, t.content, attachments, files=files)
        state_slots[i] = _slot_state(i, t, res)
    else:
        content = t.content if t.role == "content" else BUFFER_CONTENT
        res = api.edit_message(thread_id, mid, content, [])
        state_slots[i] = {"i": i, "role": t.role, "id": mid,
                          "hash": slot_hash(t),
                          "content": content}


def _apply_create(api, thread_id, op, state_slots):
    t = op["target"]
    i = op["i"]
    if t.role == "image":
        data = open(t.src, "rb").read()
        res = api.create_message(thread_id, IMAGE_CONTENT, flags=FLAG_SUPPRESS_NOTIFICATIONS,
                                 files=[(t.filename, data)],
                                 description=getattr(t, "description", ""))
    elif t.role == "snapshot":
        data = open(t.src, "rb").read()
        res = api.create_message(thread_id, t.content, flags=FLAG_SUPPRESS_NOTIFICATIONS,
                                 files=[(t.filename, data)])
    else:
        content = t.content if t.role == "content" else BUFFER_CONTENT
        flags = FLAG_SUPPRESS_NOTIFICATIONS | (FLAG_SUPPRESS_EMBEDS if t.role == "content" else 0)
        res = api.create_message(thread_id, content, flags=flags)
    state_slots.append(_slot_state(i, t, res))


def _apply_revert(api, thread_id, op, state_slots):
    i = op["i"]
    mid = op["message_id"]
    api.edit_message(thread_id, mid, BUFFER_CONTENT, [])
    state_slots[i] = {"i": i, "role": "buffer", "id": mid,
                      "hash": chunker.slot_hash_buffer(), "content": BUFFER_CONTENT}


def _recover_attachment(api, thread_id, mid, old):
    """Return attachments array to preserve. Recover from API if uncertain."""
    try:
        msg = api.get_message(thread_id, mid)
        return [{"id": a["id"], "filename": a.get("filename", ""),
                 "description": a.get("description") or ""}
                for a in msg.get("attachments", [])]
    except Exception:
        aid = old.get("attachment_id")
        if not aid:
            return []
        return [{"id": aid, "filename": old.get("filename", ""),
                 "description": old.get("description", "")}]


def _verify_or_heal(api, thread_id, mid, res, target, old):
    got = res.get("attachments") if isinstance(res, dict) else None
    if not got:
        return
    # heal handled by caller if needed; v1 trusts API response


def _slot_state(i, t, res):
    st = {"i": i, "role": t.role, "id": res.get("id"), "hash": slot_hash(t)}
    if t.role == "content":
        st["content"] = t.content
    elif t.role == "image":
        st["src"] = getattr(t, "src", None)
        st["src_hash"] = getattr(t, "src_hash", None)
        st["filename"] = getattr(t, "filename", None)
        st["description"] = getattr(t, "description", "")
        atts = res.get("attachments") or []
        if atts:
            st["attachment_id"] = atts[0]["id"]
    elif t.role == "snapshot":
        st["payload_hash"] = getattr(t, "payload_hash", None)
        st["src"] = getattr(t, "src", None)
        st["filename"] = getattr(t, "filename", None)
        atts = res.get("attachments") or []
        if atts:
            st["attachment_id"] = atts[0]["id"]
    return st
