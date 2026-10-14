import argparse
import json
import os
import sys
from pathlib import Path

from . import apply as apply_mod
from . import config as config_mod
from . import images, markdown, state as state_mod, tags
from .chunker import build_plan, diff as diff_plan


def _build_slots(note_path, cfg, text, body):
    blocks = markdown.parse_blocks(body)
    plan = build_plan(blocks, cfg={"limit": cfg.get("defaults", {}).get("limit", 1900)})
    plan = apply_mod.enrich_image_slots(plan, note_path, cfg["vault_root"])
    K = len(plan)
    policy = cfg.get("defaults", {}).get("buffer_policy", {})
    N = apply_mod.buffer_count(K, policy.get("ratio", 0.5), policy.get("min", 2), policy.get("max", 10))
    buffers = apply_mod.buffer_plans(None, N)
    snapshot = None
    if cfg.get("defaults", {}).get("snapshot_attachment", True):
        from .chunker import SlotPlan, slot_hash_snapshot
        import hashlib
        h = hashlib.sha256(markdown.nfc(body).encode("utf-8")).hexdigest()
        snapshot = SlotPlan(K + N, "snapshot", content="-# 원본 마크다운 첨부",
                            payload_hash=f"sha256:{h}",
                            src=str(note_path), filename=Path(note_path).name)
    slots = plan + buffers + ([snapshot] if snapshot else [])
    for idx, s in enumerate(slots):
        s.i = idx
    return slots, {"K": K, "N": N}


def cmd_doctor(args, cfg):
    if args.tags:
        from .discord_api import DiscordAPI
        api = DiscordAPI(cfg["_token"], cfg.get("defaults", {}))
        available = tags.fetch_available_tags(api, args.tags)
        if not available:
            print(f"no available_tags for forum {args.tags}")
            return 0
        for t in sorted(available.values(), key=lambda x: x["name"]):
            mod = " moderated" if t["moderated"] else ""
            print(f"{t['id']}  {t['name']}{mod}")
        return 0
    problems = []
    vr = Path(cfg["vault_root"])
    if not vr.exists():
        problems.append(f"vault_root not found: {vr}")
    if problems:
        for p in problems:
            print(f"FAIL {p}")
        return 5
    print(f"OK vault {vr}")
    print(f"OK modules loaded, token len {len(cfg['_token'])}")
    return 0


def _resolve_note(arg, cfg):
    from .vault import read_note
    text, path = read_note(arg, cfg["vault_root"])
    return text, path


def _print_dry_run(note_rel, action, slots, policy, ops, forum_tags):
    out = {
        "note": note_rel,
        "action": action,
        "mode": "dry-run",
        "content_hash": apply_mod.plan_content_hash(slots),
        "limit": 1900,
        "forum_tags": forum_tags,
        "plan": [{"i": s.i, "role": s.role,
                  "chars": len(getattr(s, "content", "")),
                  "hash": apply_mod.slot_hash(s)} for s in slots],
        "buffer_policy": policy,
        "ops": [{"kind": o["kind"], "i": o["i"],
                 "target_role": o.get("target_role", o.get("to"))} for o in ops],
        "warnings": [],
    }
    print(json.dumps(out, indent=2, ensure_ascii=False))


def main(argv=None):
    parser = argparse.ArgumentParser(prog="sync.py")
    parser.add_argument("cmd", nargs="?")
    parser.add_argument("note", nargs="?")
    parser.add_argument("--config", default="./config.json")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--diff", action="store_true")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--post-id")
    parser.add_argument("--starter-id")
    parser.add_argument("--tags", metavar="FORUM_ID")
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    try:
        cfg = config_mod.load_config(args.config)
    except config_mod.ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2

    if args.cmd == "doctor":
        return cmd_doctor(args, cfg)
    if args.cmd is None:
        parser.print_help()
        return 1

    if args.all:
        return _do_all(args, cfg)

    if not args.note:
        print(f"'{args.cmd}' requires a note path", file=sys.stderr)
        return 1

    try:
        text, path = _resolve_note(args.note, cfg)
    except (FileNotFoundError, ValueError) as e:
        print(f"note error: {e}", file=sys.stderr)
        return 3

    fm, body = state_mod.read_frontmatter(text)
    ds = fm.get("discord_sync") or {}
    note_rel = os.path.relpath(path, cfg["vault_root"])

    slots, policy = _build_slots(path, cfg, text, body)
    target_tags = tags.resolve_target_tags(fm, cfg, note_rel)
    try:
        tags.check_max(target_tags)
    except tags.TagError as e:
        print(f"tag error: {e}", file=sys.stderr)
        return 2

    if args.dry_run:
        index = state_mod.load_index(cfg.get("index_path", "index.json"))
        entry = index["notes"].get(note_rel, {})
        ops = diff_plan(slots, entry.get("slots", []))
        if args.diff:
            for o in ops:
                print(f"{o['kind']:16} i={o['i']:3} {o.get('from','')}->{o.get('to',o.get('target_role',''))}")
            if tags.diff_tags(target_tags, entry.get("forum_tags", [])):
                print(f"{'set_tags':16} {target_tags}")
        else:
            _print_dry_run(note_rel, "sync", slots, policy, ops, target_tags)
        return 0

    if args.cmd == "status":
        index = state_mod.load_index(cfg.get("index_path", "index.json"))
        entry = index["notes"].get(note_rel, {})
        ops = diff_plan(slots, entry.get("slots", []))
        for o in ops:
            print(f"{o['kind']:16} i={o['i']:3} {o.get('from','')}->{o.get('to',o.get('target_role',''))}")
        print(f"{len(ops)} pending ops, {len(slots)} planned slots")
        return 0
    if args.cmd == "adopt":
        return _do_adopt(args, cfg, path, note_rel, body, fm, ds, slots)
    if args.cmd == "verify":
        return _do_verify(args, cfg, note_rel, slots)
    if args.cmd == "rebalance":
        if not args.yes:
            print("rebalance requires --yes (loses comments/deeplinks)", file=sys.stderr)
            return 1
        return _do_rebalance(args, cfg, path, note_rel, body, fm, ds, slots)
    if args.cmd == "publish":
        return _do_publish(args, cfg, path, note_rel, text, body, fm, ds, slots, policy)
    if args.cmd == "sync":
        return _do_sync(args, cfg, path, note_rel, text, body, fm, ds, slots, policy)
    print(f"unknown command: {args.cmd}", file=sys.stderr)
    return 1


def _do_all(args, cfg):
    root = Path(cfg["vault_root"])
    rc = 0
    for p in sorted(root.rglob("*.md")):
        try:
            text, path = _resolve_note(str(p), cfg)
        except (FileNotFoundError, ValueError):
            continue
        fm, body = state_mod.read_frontmatter(text)
        ds = fm.get("discord_sync") or {}
        if not ds.get("enabled"):
            continue
        sub = [] if not args.dry_run else ["--dry-run"]
        if args.diff:
            sub.append("--diff")
        sub.append(args.cmd)
        sub.append(str(os.path.relpath(p, root)))
        sub += ["--config", args.config]
        print(f"== {os.path.relpath(p, root)}")
        rc = max(rc, main(sub))
    return rc


def _do_adopt(args, cfg, path, note_rel, body, fm, ds, slots):
    from .discord_api import DiscordAPI
    if not args.post_id:
        print("adopt requires --post-id", file=sys.stderr)
        return 1
    api = DiscordAPI(cfg["_token"], cfg.get("defaults", {}))
    thread_id = args.post_id
    state_slots = []
    i = 0
    while True:
        msgs = _list_messages(api, thread_id, i)
        if not msgs:
            break
        for m in msgs:
            state_slots.append({
                "i": len(state_slots), "role": "content", "id": m["id"],
                "hash": "sha256:unknown", "content": m.get("content", ""),
            })
        i += len(msgs)
        break
    _persist(cfg, note_rel, path, fm, ds, body, thread_id, state_slots, slots)
    print(f"adopted {note_rel} -> {thread_id} ({len(state_slots)} messages)")
    return 0


def _do_verify(args, cfg, note_rel, slots):
    from .discord_api import DiscordAPI
    index = state_mod.load_index(cfg.get("index_path", "index.json"))
    entry = index["notes"].get(note_rel)
    if not entry:
        print(f"no state for {note_rel}", file=sys.stderr)
        return 5
    api = DiscordAPI(cfg["_token"], cfg.get("defaults", {}))
    thread_id = entry["thread_id"]
    mismatches = 0
    for s in entry.get("slots", []):
        try:
            msg = api.get_message(thread_id, s["id"])
        except Exception as e:  # noqa: BLE001
            print(f"MISSING slot {s['i']} id={s['id']}: {e}")
            mismatches += 1
            continue
        if s["role"] == "image":
            atts = msg.get("attachments") or []
            if not atts:
                print(f"IMAGE LOST slot {s['i']} id={s['id']}")
                mismatches += 1
    print(f"verify {note_rel}: {mismatches} mismatches")
    return 0 if not mismatches else 5


def _do_rebalance(args, cfg, path, note_rel, body, fm, ds, slots):
    from .discord_api import DiscordAPI
    index = state_mod.load_index(cfg.get("index_path", "index.json"))
    entry = index["notes"].get(note_rel, {})
    old_thread = entry.get("thread_id")
    api = DiscordAPI(cfg["_token"], cfg.get("defaults", {}))
    forum_id = _resolve_forum_id(ds, cfg, note_rel, entry)
    title = fm.get("title") or Path(path).stem
    thread = api.create_thread(forum_id, title[:100], {"content": f"# {title}\n"})
    thread_id = thread["id"]
    state_slots = []
    for s in slots:
        apply_mod._apply_create(api, thread_id, {"kind": "create", "i": s.i, "target": s}, state_slots)
    if old_thread:
        try:
            api.create_message(old_thread, "이 글은 재게시되었습니다. 새 글을 참고하세요.", flags=4096)
            api.edit_channel(old_thread, {"archived": True, "locked": True})
        except Exception:  # noqa: BLE001
            pass
    _persist(cfg, note_rel, path, fm, ds, body, thread_id, state_slots, slots, forum_id=forum_id)
    print(f"rebalanced {note_rel}: {old_thread} -> {thread_id}")
    return 0


def _list_messages(api, channel_id, after=0):
    return api._request("GET", f"/channels/{channel_id}/messages?limit=100") or []


def _resolve_applied_tags(api, forum_id, target_tags):
    """Validate desired tag IDs against the forum's available_tags.

    Returns (applied_ids, warnings). Unknown IDs are dropped with a warning.
    """
    if not target_tags:
        return [], []
    available = tags.fetch_available_tags(api, forum_id)
    known, unknown, _ = tags.validate(target_tags, available)
    warnings = [f"unknown forum tag id dropped: {u}" for u in unknown]
    return known, warnings


def _do_publish(args, cfg, path, note_rel, text, body, fm, ds, slots, policy):
    from .discord_api import DiscordAPI, DiscordError
    forum_id = _resolve_forum_id(ds, cfg, note_rel)
    if not forum_id:
        print("no forum_id (skip)", file=sys.stderr)
        return 2
    api = DiscordAPI(cfg["_token"], cfg.get("defaults", {}))
    title = fm.get("title") or Path(path).stem
    starter = f"# {title}\n\n-# 마지막 동기화: (게시 직후)\n"

    target_tags = tags.resolve_target_tags(fm, cfg, note_rel)
    applied, warnings = _resolve_applied_tags(api, forum_id, target_tags)
    for w in warnings:
        print(w, file=sys.stderr)

    try:
        thread = api.create_thread(forum_id, title[:100], {"content": starter},
                                   applied_tags=applied or None)
    except DiscordError as e:
        if applied:
            print(f"tag apply failed ({e}); retrying without tags", file=sys.stderr)
            applied = []
            thread = api.create_thread(forum_id, title[:100], {"content": starter})
        else:
            raise
    thread_id = thread["id"]

    state_slots = []
    failures = []
    for s in slots:
        op = {"kind": "create", "i": s.i, "target": s}
        try:
            apply_mod._apply_create(api, thread_id, op, state_slots)
        except Exception as e:  # noqa: BLE001
            failures.append({"i": s.i, "error": str(e)})

    _persist(cfg, note_rel, path, fm, ds, body, thread_id, state_slots, slots,
             forum_tags=applied, forum_id=forum_id)
    print(f"published {note_rel} -> thread {thread_id} ({len(state_slots)} slots, {len(failures)} failures)")
    return 0 if not failures else 4


def _do_sync(args, cfg, path, note_rel, text, body, fm, ds, slots, policy):
    from .discord_api import DiscordAPI
    index = state_mod.load_index(cfg.get("index_path", "index.json"))
    entry = index["notes"].get(note_rel)
    if not entry or not (ds.get("post_id") or entry.get("thread_id")):
        print(f"{note_rel} not published yet (no post_id)", file=sys.stderr)
        return 5
    thread_id = ds.get("post_id") or entry["thread_id"]
    state_slots = entry.get("slots", [])
    forum_id = _resolve_forum_id(ds, cfg, note_rel, entry)

    target_tags = tags.resolve_target_tags(fm, cfg, note_rel)
    applied_now = entry.get("forum_tags", [])
    tags_changed = tags.diff_tags(target_tags, applied_now)

    ops = diff_plan(slots, state_slots)
    if not ops and not tags_changed:
        print(f"no-op: {note_rel}")
        return 0

    api = DiscordAPI(cfg["_token"], cfg.get("defaults", {}))
    if tags_changed:
        from .discord_api import DiscordError
        applied, warnings = _resolve_applied_tags(api, forum_id, target_tags)
        for w in warnings:
            print(w, file=sys.stderr)
        if tags.diff_tags(applied, applied_now):
            try:
                api.edit_channel(thread_id, {"applied_tags": applied})
            except DiscordError as e:
                print(f"tag apply failed ({e}); clearing tags", file=sys.stderr)
                applied = []
                api.edit_channel(thread_id, {"applied_tags": []})
        applied_now = applied

    if ops:
        api.edit_channel(thread_id, {"archived": False, "locked": False})
        _, failures = apply_mod.apply_ops(api, thread_id, ops, state_slots)
    else:
        failures = []

    _persist(cfg, note_rel, path, fm, ds, body, thread_id, state_slots, slots,
             forum_tags=applied_now, forum_id=forum_id)
    print(f"synced {note_rel} ({len(ops)} ops, {len(failures)} failures)")
    return 0 if not failures else 4


def _persist(cfg, note_rel, path, fm, ds, body, thread_id, state_slots, slots,
             forum_tags=None, forum_id=None):
    from .discord_api import DiscordAPI  # noqa: F401
    new_ds = dict(ds)
    new_ds["enabled"] = True
    new_ds["post_id"] = str(thread_id)
    new_ds["content_hash"] = apply_mod.plan_content_hash(slots)
    import datetime
    new_ds["synced_at"] = datetime.datetime.now().astimezone().isoformat()
    new_ds["buffers"] = [s["id"] for s in state_slots if s["role"] == "buffer"]
    state_mod.update_note_frontmatter(path, new_ds, body)
    index = state_mod.load_index(cfg.get("index_path", "index.json"))
    existing = index["notes"].get(note_rel, {})
    if forum_tags is None:
        forum_tags = existing.get("forum_tags", [])
    if forum_id is None:
        forum_id = new_ds.get("forum_id") or existing.get("forum_id")
    index["notes"][note_rel] = {
        "forum_id": forum_id,
        "thread_id": str(thread_id),
        "content_hash": new_ds["content_hash"],
        "synced_at": new_ds["synced_at"],
        "forum_tags": list(forum_tags),
        "slots": [
            {k: v for k, v in s.items() if v is not None}
            for s in state_slots
        ],
    }
    state_mod.save_index(cfg.get("index_path", "index.json"), index)


def _forum_from_config(cfg, note_rel):
    notes = cfg.get("notes", {})
    if note_rel in notes:
        return notes[note_rel].get("forum_id")
    return cfg.get("defaults", {}).get("forum_id")


def _resolve_forum_id(ds, cfg, note_rel, entry=None):
    fid = ds.get("forum_id") or _forum_from_config(cfg, note_rel)
    if not fid and entry:
        fid = entry.get("forum_id")
    return fid
