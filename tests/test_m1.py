"""M1 tests (no network). Run: python -m tests.test_m1"""
import os
import sys
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sync import markdown, chunker, vault, apply


def _plan(text, **cfg):
    blocks = markdown.parse_blocks(text)
    return chunker.build_plan(blocks, cfg=cfg or {'limit': 1900})


def u1_short_paragraph():
    plan = _plan("hi")
    assert len(plan) == 1
    assert plan[0].role == "content"
    assert plan[0].content == "hi"


def u2_boundary():
    plan = _plan("x" * 1900)
    assert len(plan) == 1
    plan = _plan("x" * 1901)
    assert len(plan) == 2


def u3_long_paragraph_splits():
    plan = _plan("a" * 3000)
    assert len(plan) >= 2
    for s in plan:
        assert chunker.utf16_len(s.content) <= 1900


def u4_codefence_atomic():
    body = "```\n" + ("y" * 50) + "\n```"
    blocks = markdown.parse_blocks("before\n\n" + body)
    plan = chunker.build_plan(blocks, cfg={"limit": 1900})
    kinds = [b["type"] for b in blocks]
    assert "codefence" in kinds
    assert any(s.role == "content" and "```" in s.content for s in plan)


def u5_table_not_split():
    tbl = "| a | b |\n|---|---|\n| 1 | 2 |"
    plan = _plan(tbl)
    assert len(plan) == 1
    assert "| a | b |" in plan[0].content


def u6_image_placements():
    assert [s.role for s in _plan("![[i.png]]")] == ["image"]
    assert [s.role for s in _plan("A\n\n![[i.png]]\n\nB")] == ["content", "image", "content"]
    assert [s.role for s in _plan("![[i.png]]\n\nhi")] == ["image", "content"]
    assert [s.role for s in _plan("![[i1.png]]\n\n![[i2.png]]")] == ["image", "image"]


def u7_nfd_to_nfc():
    import tempfile
    s_nfc = "한글테스트"
    s_nfd = unicodedata.normalize("NFD", s_nfc)
    assert s_nfd != s_nfc
    f = tempfile.NamedTemporaryFile(mode="w", delete=False, encoding="utf-8")
    f.write(s_nfd)
    f.close()
    try:
        txt, _ = vault.read_note(f.name)
        assert txt == s_nfc
        assert chunker.utf16_len(txt) == len(s_nfc)
    finally:
        os.unlink(f.name)


def u8_astral_emoji():
    assert chunker.utf16_len("😀") == 2
    assert chunker.utf16_len("a😀b") == 4


def u9_conversions():
    import tempfile
    from sync import cli

    d = tempfile.mkdtemp()
    note = os.path.join(d, "n.md")
    body = ("[[Note]] [[Note|show]]\n\n"
            "> [!note] Title\n> body\n\n"
            "==hl==\n\n"
            "inline $x$ math\n\n"
            "footnote[^1]\n")
    text = "---\ntitle: N\n---\n\n" + body
    with open(note, "w", encoding="utf-8") as f:
        f.write(text)

    cfg = {"vault_root": d, "defaults": {"limit": 1900}}
    slots, _, stats = cli._build_slots(note, cfg, text, body)
    joined = "\n".join(s.content for s in slots)
    assert "[[" not in joined, joined
    assert "> **Title**" in joined and "> body" in joined
    assert "==hl==" not in joined and "hl" in joined
    assert "$x$" not in joined
    assert "[^1]" not in joined
    assert "Note" in joined and "show" in joined
    assert stats["text"] >= 2  # both unresolved wiki links fell back to text


def u10_diff_shrink_role_change():
    plan = _plan("tiny")
    hash_content = chunker.slot_hash_content(plan[0].content)
    state_slots = [
        {"i": 0, "role": "content", "id": "1", "hash": hash_content},
        {"i": 1, "role": "image", "id": "2", "hash": "sha256:deadbeef"},
        {"i": 2, "role": "buffer", "id": "3", "hash": chunker.slot_hash_buffer()},
    ]
    ops = chunker.diff(plan, state_slots)
    assert len(ops) == 1
    assert ops[0]["kind"] == "revert_to_buffer"
    assert ops[0]["message_id"] == "2"
    # never deletes
    assert all(o["kind"] != "delete" for o in ops)


def u10b_diff_noop():
    plan = _plan("tiny")
    h = chunker.slot_hash_content(plan[0].content)
    state_slots = [{"i": 0, "role": "content", "id": "1", "hash": h}]
    ops = chunker.diff(plan, state_slots)
    assert ops == []


def u11_buffer_policy():
    assert apply.buffer_count(2) == 2
    assert apply.buffer_count(12) == 6
    assert apply.buffer_count(20) == 10
    assert apply.buffer_count(30) == 10


def u12_determinism():
    text = "# H\n\npara one\n\n![[a.png]]\n\npara two"
    p1 = _plan(text)
    p2 = _plan(text)
    assert [s.role for s in p1] == [s.role for s in p2]
    assert [s.content for s in p1] == [s.content for s in p2]
    h1 = chunker.plan_content_hash(p1)
    h2 = chunker.plan_content_hash(p2)
    assert h1 == h2


def udry_dry_run_zero_http():
    import json as _json
    import tempfile
    from sync import cli, discord_api

    d = tempfile.mkdtemp()
    vault = os.path.join(d, "vault")
    os.makedirs(os.path.join(vault, "pages"), exist_ok=True)
    note = os.path.join(vault, "pages", "n.md")
    with open(note, "w", encoding="utf-8") as f:
        f.write("---\ntitle: N\ndiscord_sync:\n  enabled: true\n---\n\nhello\n")
    cfg_path = os.path.join(d, "config.json")
    with open(cfg_path, "w", encoding="utf-8") as f:
        _json.dump({"vault_root": vault, "index_path": os.path.join(d, "index.json"),
                    "defaults": {"limit": 1900}}, f)

    original = discord_api.DiscordAPI

    class Boom:
        def __init__(self, *a, **kw):
            raise AssertionError("dry-run must not construct DiscordAPI")

    import contextlib
    import io
    discord_api.DiscordAPI = Boom
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            rc = cli.main(["sync", "pages/n.md", "--dry-run", "--config", cfg_path])
        assert rc == 0
    finally:
        discord_api.DiscordAPI = original


def u14_snapshot_uploads_attachment():
    import tempfile
    from sync import apply, chunker as ch

    class FakeAPI:
        def __init__(self):
            self.calls = []

        def create_message(self, thread_id, content, flags=4096, files=None, description=None):
            self.calls.append(files)
            return {"id": "100", "attachments": [{"id": "a1"}] if files else []}

    f = tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False)
    f.write("orig")
    f.close()
    try:
        api = FakeAPI()
        slot = ch.SlotPlan(0, "snapshot", content="-# 원본", payload_hash="sha256:x",
                           src=f.name, filename="UBL.md")
        state = []
        apply._apply_create(api, "T", {"kind": "create", "i": 0, "target": slot}, state)
        assert api.calls[0] is not None, "snapshot slot must upload an attachment"
        assert api.calls[0][0][0] == "UBL.md"
        assert state[0].get("attachment_id") == "a1"
    finally:
        os.unlink(f.name)


def u15_snapshot_hash_stable_across_persist():
    from sync import cli, state as state_mod, apply as apply_mod

    body = "# T\n\nhello\n"
    cfg = {"vault_root": "/tmp", "defaults": {}}
    fm1 = {"discord_sync": {"enabled": True, "post_id": "1"}}
    text1 = "---\n" + __import__("yaml").safe_dump(fm1, allow_unicode=True) + "---\n" + body
    fm2 = {"discord_sync": {"enabled": True, "post_id": "1",
                            "synced_at": "2026-10-15T00:00:00+09:00",
                            "content_hash": "sha256:changed"}}
    text2 = "---\n" + __import__("yaml").safe_dump(fm2, allow_unicode=True) + "---\n" + body

    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "_tmp_note.md")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(body)
    try:
        s1, _, _ = cli._build_slots(path, cfg, text1, body)
        s2, _, _ = cli._build_slots(path, cfg, text2, body)
        h1 = apply_mod.plan_content_hash(s1)
        h2 = apply_mod.plan_content_hash(s2)
        assert h1 == h2, "frontmatter churn must not change plan hash (no-op must hold)"
    finally:
        os.unlink(path)


def u16_image_slot_uploads():
    import tempfile
    from sync import markdown as md, chunker as ch, apply as ap

    d = tempfile.mkdtemp()
    vault = os.path.join(d, "vault")
    os.makedirs(os.path.join(vault, "raw"), exist_ok=True)
    img = os.path.join(vault, "raw", "pic.png")
    with open(img, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
    note = os.path.join(vault, "n.md")
    with open(note, "w", encoding="utf-8") as f:
        f.write("A\n\n![[raw/pic.png]]\n\nB\n")

    blocks = md.parse_blocks("A\n\n![[raw/pic.png]]\n\nB\n")
    plan = ap.enrich_image_slots(ch.build_plan(blocks), note, vault)
    slot = [s for s in plan if s.role == "image"][0]
    assert slot.filename == "pic.png", "enrich_image_slots must set filename"

    class FakeAPI:
        def __init__(self):
            self.calls = []

        def create_message(self, *a, files=None, **kw):
            self.calls.append(files)
            return {"id": "9", "attachments": [{"id": "a1"}]}

    api = FakeAPI()
    state = []
    ap._apply_create(api, "T", {"kind": "create", "i": 1, "target": slot}, state)
    assert api.calls[0] and api.calls[0][0][0] == "pic.png"
    assert state[0].get("attachment_id") == "a1"


def u13_tag_resolution_priority():
    from sync import tags

    cfg = {"defaults": {"forum_tags": ["d"]},
           "notes": {"n.md": {"forum_tags": ["c"]}}}
    assert tags.resolve_target_tags({"discord_sync": {"forum_tags": ["f"]}}, cfg, "n.md") == ["f"]
    assert tags.resolve_target_tags({"discord_sync": {}}, cfg, "n.md") == ["c"]
    assert tags.resolve_target_tags({"discord_sync": {}}, cfg, "other.md") == ["d"]
    assert tags.resolve_target_tags({}, {"defaults": {}}, "x.md") == []
    # explicit empty overrides lower priority
    assert tags.resolve_target_tags({"discord_sync": {"forum_tags": []}}, cfg, "n.md") == []


def u14_tag_validation():
    from sync import tags

    try:
        tags.check_max(["1", "2", "3", "4", "5", "6"])
        assert False, ">5 must raise"
    except tags.TagError:
        pass
    tags.check_max(["1", "2", "3", "4", "5"])  # ok

    available = {"1": {"id": "1", "name": "a", "moderated": False},
                 "2": {"id": "2", "name": "b", "moderated": True}}
    known, unknown, _ = tags.validate(["1", "2", "9"], available)
    assert known == ["1", "2"]
    assert unknown == ["9"]

    class FakeAPI:
        calls = 0

        def get_channel(self, fid):
            FakeAPI.calls += 1
            return {"id": fid, "available_tags": [{"id": "1", "name": "a", "moderated": False}]}

    from sync import cli
    applied, warnings = cli._resolve_applied_tags(FakeAPI(), "F", ["1", "9"])
    assert applied == ["1"]
    assert any("9" in w for w in warnings)
    assert FakeAPI.calls == 1


def u17_publish_applies_valid_tags():
    import json as _json
    import tempfile
    from sync import cli, discord_api, state as state_mod

    d = tempfile.mkdtemp()
    vault = os.path.join(d, "vault")
    os.makedirs(os.path.join(vault, "pages"), exist_ok=True)
    note = os.path.join(vault, "pages", "t.md")
    with open(note, "w", encoding="utf-8") as f:
        f.write("---\ntitle: T\ndiscord_sync:\n  enabled: true\n"
                "  forum_tags: ['100', '999']\n---\n\nhello\n")
    idx = os.path.join(d, "index.json")
    cfg_path = os.path.join(d, "config.json")
    with open(cfg_path, "w", encoding="utf-8") as f:
        _json.dump({"vault_root": vault, "index_path": idx,
                    "defaults": {"forum_id": "F", "limit": 1900}}, f)

    class FakeAPI:
        def __init__(self, *a, **kw):
            self.calls = []
            self.applied = None

        def get_channel(self, fid):
            return {"available_tags": [{"id": "100", "name": "a", "moderated": False}]}

        def create_thread(self, forum_id, name, message, applied_tags=None, flags=4096):
            self.applied = applied_tags
            return {"id": "T1"}

        def create_message(self, *a, **kw):
            return {"id": "m%d" % len(self.calls), "attachments": []}

    holder = {}

    def factory(token, cfg=None):
        holder["api"] = FakeAPI()
        return holder["api"]

    import contextlib
    import io
    original = discord_api.DiscordAPI
    discord_api.DiscordAPI = factory
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            rc = cli.main(["publish", "pages/t.md", "--config", cfg_path])
        assert rc == 0, rc
        assert holder["api"].applied == ["100"], holder["api"].applied
        index = state_mod.load_index(idx)
        assert index["notes"]["pages/t.md"]["forum_tags"] == ["100"]
    finally:
        discord_api.DiscordAPI = original


def u18_config_notes_forum_id_resolves_in_sync():
    import json as _json
    import tempfile
    import contextlib
    import io
    from sync import cli, discord_api, state as state_mod

    d = tempfile.mkdtemp()
    vault = os.path.join(d, "vault")
    os.makedirs(os.path.join(vault, "pages"), exist_ok=True)
    note = os.path.join(vault, "pages", "fic.md")
    open(note, "w", encoding="utf-8").write(
        "---\ntitle: Fic\ndiscord_sync:\n  enabled: true\n---\n\nhello\n")
    idx = os.path.join(d, "index.json")
    cfg_path = os.path.join(d, "config.json")
    _json.dump({"vault_root": vault, "index_path": idx,
                "notes": {"pages/fic.md": {"forum_id": "FORUM9"}},
                "defaults": {"forum_id": None, "limit": 1900}}, open(cfg_path, "w"))

    seen = {}

    class Fake:
        def __init__(self, *a, **k):
            pass

        def get_channel(self, f):
            seen["forum_id"] = f
            return {"available_tags": [{"id": "100", "name": "a", "moderated": False}]}

        def create_thread(self, f, *a, **k):
            seen["thread_forum"] = f
            return {"id": "T1"}

        def create_message(self, *a, **k):
            return {"id": "m", "attachments": []}

        def edit_channel(self, *a, **k):
            return {}

        def edit_message(self, ch, mid, c, att, files=None, flags=None):
            return {"id": mid, "attachments": []}

        def get_message(self, ch, mid):
            return {"id": mid, "attachments": []}

    original = discord_api.DiscordAPI
    discord_api.DiscordAPI = lambda t, c=None: Fake()
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            cli.main(["publish", "pages/fic.md", "--config", cfg_path])
        index = state_mod.load_index(idx)
        assert index["notes"]["pages/fic.md"]["forum_id"] == "FORUM9"

        open(note, "w", encoding="utf-8").write(
            "---\ntitle: Fic\ndiscord_sync:\n  enabled: true\n  forum_tags: ['100']\n---\n\nhello\n")
        with contextlib.redirect_stdout(io.StringIO()):
            rc = cli.main(["sync", "pages/fic.md", "--config", cfg_path])
        assert rc == 0
        assert seen["forum_id"] == "FORUM9", seen
    finally:
        discord_api.DiscordAPI = original


def u19_thread_title_sync():
    import json as _json
    import tempfile
    import contextlib
    import io
    from sync import cli, discord_api, state as state_mod
    from pathlib import Path

    # desired title: frontmatter title, else stem; truncated to 100 code points
    assert cli._desired_title({"title": "UBL"}, "/x/ignored.md") == "UBL"
    assert cli._desired_title({}, "/x/My Note.md") == "My Note"
    assert cli._desired_title({"title": "a" * 150}, "/x/n.md") == "a" * 100

    d = tempfile.mkdtemp()
    vault = os.path.join(d, "vault")
    os.makedirs(os.path.join(vault, "pages"), exist_ok=True)
    note = os.path.join(vault, "pages", "ttl.md")
    open(note, "w", encoding="utf-8").write(
        "---\ntitle: UBL\ndiscord_sync:\n  enabled: true\n---\n\nhello\n")
    idx = os.path.join(d, "index.json")
    cfg_path = os.path.join(d, "config.json")
    _json.dump({"vault_root": vault, "index_path": idx,
                "defaults": {"forum_id": "F1", "limit": 1900}},
               open(cfg_path, "w"))

    state = {"constructed": 0, "name_patches": [], "starter_patches": []}

    class Fake:
        def __init__(self, *a, **k):
            state["constructed"] += 1

        def get_channel(self, f):
            return {"available_tags": []}

        def create_thread(self, f, name, *a, **k):
            return {"id": "T1", "name": name}

        def create_message(self, *a, **k):
            return {"id": "m", "attachments": []}

        def edit_channel(self, ch, payload):
            if "name" in payload:
                state["name_patches"].append(payload["name"])
            return {"name": payload.get("name", "T1")}

        def edit_message(self, ch, mid, c, att, files=None, flags=None):
            state["starter_patches"].append(c)
            return {"id": mid, "attachments": []}

        def get_message(self, ch, mid):
            return {"id": mid, "attachments": []}

    original = discord_api.DiscordAPI
    discord_api.DiscordAPI = lambda t, c=None: Fake()
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            cli.main(["publish", "pages/ttl.md", "--config", cfg_path])
        assert state_mod.load_index(idx)["notes"]["pages/ttl.md"]["thread_name"] == "UBL"

        # change title -> exactly one name PATCH, index updated to returned name
        open(note, "w", encoding="utf-8").write(
            "---\ntitle: UBL 개요\ndiscord_sync:\n  enabled: true\n---\n\nhello\n")
        # --diff must show the pending title change, still 0 HTTP
        buf = io.StringIO()
        before = state["constructed"]
        with contextlib.redirect_stdout(buf):
            rc = cli.main(["sync", "pages/ttl.md", "--dry-run", "--diff", "--config", cfg_path])
        assert rc == 0 and state["constructed"] == before
        assert "set_title" in buf.getvalue(), buf.getvalue()

        with contextlib.redirect_stdout(io.StringIO()):
            rc = cli.main(["sync", "pages/ttl.md", "--config", cfg_path])
        assert rc == 0
        assert state["name_patches"] == ["UBL 개요"], state
        assert state["starter_patches"] == ["# UBL 개요\n\n-# 마지막 동기화: (게시 직후)\n"], state
        assert state_mod.load_index(idx)["notes"]["pages/ttl.md"]["thread_name"] == "UBL 개요"

        # immediate rerun -> 0 HTTP (no DiscordAPI constructed)
        before = state["constructed"]
        with contextlib.redirect_stdout(io.StringIO()):
            rc = cli.main(["sync", "pages/ttl.md", "--config", cfg_path])
        assert rc == 0 and state["constructed"] == before
        assert len(state["name_patches"]) == 1

        # revert -> one more PATCH
        open(note, "w", encoding="utf-8").write(
            "---\ntitle: UBL\ndiscord_sync:\n  enabled: true\n---\n\nhello\n")
        with contextlib.redirect_stdout(io.StringIO()):
            cli.main(["sync", "pages/ttl.md", "--config", cfg_path])
        assert state["name_patches"] == ["UBL 개요", "UBL"]
        assert state["starter_patches"] == [
            "# UBL 개요\n\n-# 마지막 동기화: (게시 직후)\n",
            "# UBL\n\n-# 마지막 동기화: (게시 직후)\n",
        ], state

        # dry-run compares against stored thread_name only (0 HTTP), reports action
        buf = io.StringIO()
        before = state["constructed"]
        with contextlib.redirect_stdout(buf):
            rc = cli.main(["sync", "pages/ttl.md", "--dry-run", "--json", "--config", cfg_path])
        assert rc == 0 and state["constructed"] == before
        out = _json.loads(buf.getvalue())
        assert out["thread_name"] == {"desired": "UBL", "applied": "UBL", "action": "noop"}
        assert out["wiki_links"] == {"discord": 0, "garden": 0, "text": 0, "unresolved": []}
    finally:
        discord_api.DiscordAPI = original


def u20_wiki_link_ladder():
    from sync import cli
    from sync import markdown as md

    fixtures = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "fixtures", "wiki")
    index = {"notes": {
        "pages/target.md": {"thread_id": "T1", "guild_id": "G1"},
        "pages/noguild.md": {"thread_id": "T2"},
        "pages/rent.md": {"thread_id": "T3"},
    }}
    cfg = {
        "vault_root": fixtures,
        "defaults": {"wiki_link_mode": "discord", "wiki_link_style": "url",
                     "wiki_link_fallback": "garden", "garden_base_url": "https://g"},
        "notes": {"pages/rent.md": {"guild_id": "GN"}},
    }
    ctx = cli._wiki_link_ctx(cfg, index)

    def conv(txt, **over):
        c = dict(ctx)
        c.update(over)
        stats = md.new_wiki_stats()
        return md.convert_obsidian_to_discord(txt, {}, c, stats), stats

    out, st = conv("[[target]]")
    assert out == "[target](https://discord.com/channels/G1/T1)", out
    assert st["discord"] == 1

    out, st = conv("[[target|보기]]")
    assert out == "[보기](https://discord.com/channels/G1/T1)", out

    out, st = conv("[[target#헤딩]]")
    assert out == "[target](https://discord.com/channels/G1/T1)", out

    out, st = conv("[[target]]", style="mention")
    assert out == "<#T1>", out

    out, st = conv("[[rent]]")
    assert out == "[rent](https://discord.com/channels/GN/T3)", out

    out, st = conv("[[noguild]]")
    assert out == "noguild" and st["text"] == 1
    assert any("guild_id" in w for w in st["warnings"])

    out, st = conv("[[garden]]")
    assert out == "[garden](https://g/garden-path)", out and st["garden"] == 1

    out, st = conv("[[ascii]]")
    assert out == "[ascii](https://g/ascii)", out

    out, st = conv("[[한글]]")
    assert out == "한글" and st["text"] == 1

    out, st = conv("[[dup]]")
    assert out == "dup" and any("ambiguous" in w for w in st["warnings"])

    out, st = conv("[[garden]]", mode="garden")
    assert out == "[garden](https://g/garden-path)", out

    out, st = conv("[[target|보기]]", mode="text")
    assert out == "보기" and st["text"] == 1

    out, st = conv("[[noguild]]", defaults_guild_id="GD")
    assert out == "[noguild](https://discord.com/channels/GD/T2)", out

    a, _ = conv("[[target]] [[garden]] [[noguild]]")
    b, _ = conv("[[target]] [[garden]] [[noguild]]")
    assert a == b


def u21_wiki_syntax_edges():
    from sync import markdown as md

    stats = md.new_wiki_stats()
    out = md.convert_obsidian_to_discord("![[img.png]] [[target]]", {}, None, stats)
    assert out == "![[img.png]] target", out
    assert stats["text"] == 1

    stats = md.new_wiki_stats()
    out = md.convert_obsidian_to_discord("text $a$ and $$b$$ and ==hl==", {}, None, stats)
    assert "$" not in out and "==" not in out and "text" in out, out

    stats = md.new_wiki_stats()
    out = md.convert_obsidian_to_discord("보기[^1] 끝", {}, None, stats)
    assert "[^1]" not in out, out

    stats = md.new_wiki_stats()
    out = md.convert_obsidian_to_discord("> [!note] 제목\n> 본문", {}, None, stats)
    assert "> **제목**" in out and "> 본문" in out, out


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("u") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {t.__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
