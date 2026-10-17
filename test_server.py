"""Run: python3 test_server.py"""
import json
import os
import sqlite3
import tempfile
import time
from pathlib import Path

import server

with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    r0, r1 = tmp / "r0", tmp / "r1"
    server.CLAUDE = [r0, r1]
    (r0 / "proj").mkdir(parents=True)
    f = r0 / "proj/abc.jsonl"
    recs = [
        {"type": "user", "uuid": "u1", "cwd": "/x", "message": {"content": "hello"}},
        {"type": "mode", "mode": "normal"},
        {"type": "assistant", "uuid": "a1", "message": {"content": [
            {"type": "thinking", "thinking": ""},
            {"type": "text", "text": "hi"},
            {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}}]}},
        {"type": "user", "uuid": "u2", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1", "is_error": True,
             "content": [{"type": "text", "text": "boom"}, {"type": "image", "source": {"data": "AAAA"}}]}]}},
    ]
    f.write_text("".join(json.dumps(r) + "\n" for r in recs) + '{"type": "user", "uuid": "half')

    events, cursor = server.claude_tail("0/proj/abc", 0)
    assert [e["kind"] for e in events] == ["user", "assistant", "tool", "result"], events
    assert events[2]["id"] == events[3]["tool"] == "t1"
    assert events[3]["output"] == "boom\n[image]" and events[3]["error"]
    # the half-written last line is not consumed, and re-polling yields nothing new
    assert cursor == f.stat().st_size - len('{"type": "user", "uuid": "half')
    assert server.claude_tail("0/proj/abc", cursor) == ([], cursor)

    assert server.claude_info(f)["title"] == "hello" and server.claude_info(f)["cwd"] == "/x"

    # /clear leaves a new file holding only the command; it is not listed until a prompt arrives
    stub = f.with_name("stub.jsonl")
    stub.write_text(json.dumps({"type": "user", "cwd": "/x", "message": {"content": "<command-name>/clear</command-name>"}}) + "\n")
    assert server.claude_info(stub)["empty"] and not server.claude_info(f)["empty"]
    assert "0/proj/stub" not in [s["id"] for s in server.claude_sessions()]

    # a message queued while the agent works is only an attachment; show it as a user turn,
    # but not the task notifications that share the attachment type
    queued = lambda **a: server.claude_events({"type": "attachment", "attachment": {"type": "queued_command", **a}}, 0)[0]
    q = queued(prompt="wait", commandMode="prompt", origin={"kind": "human"})
    assert (q["kind"], q["text"]) == ("user", "wait"), q
    assert queued(prompt="<task-notification>", commandMode="task-notification")["kind"] == "meta"

    # the same relative path in two roots gives two sessions with distinct ids
    (r1 / "proj").mkdir(parents=True)
    (r1 / "proj/abc.jsonl").write_text(json.dumps({"type": "user", "uuid": "o1", "message": {"content": "other"}}) + "\n")
    (tmp / "secret.jsonl").write_text("{}\n")
    assert sorted(s["id"] for s in server.claude_sessions()) == ["0/proj/abc", "1/proj/abc"]
    assert server.claude_tail("1/proj/abc", 0)[0][0]["text"] == "other"

    # waiting for the human: a running process whose status file says idle or waiting, not busy or gone
    (tmp / "sessions").mkdir()
    for status, pid, want in [("waiting", os.getpid(), True), ("idle", os.getpid(), True),
                              ("busy", os.getpid(), False), ("idle", 2 ** 22 + 1, False)]:  # above pid_max
        (tmp / "sessions/1.json").write_text(json.dumps({"pid": pid, "sessionId": "abc", "status": status}))
        assert [s["waiting"] for s in server.claude_sessions()] == [want, want], (status, pid)

    # ids are untrusted: each must stay inside the root it names, and name a root that exists
    for bad in ["0/../r1/proj/abc", "0/../secret", "1/../r0/proj/abc", "0/../../etc/passwd", f"0/{r1}/proj/abc",
                "../../etc/passwd", "proj/abc", "2/proj/abc", "-1/proj/abc", "x/proj/abc", "", "/"]:
        try:
            server.claude_tail(bad, 0)
            raise AssertionError(f"bad id allowed: {bad!r}")
        except ValueError:
            pass

    # opencode: the id prefix picks the database
    dbs = [tmp / "a.db", tmp / "b.db"]
    for n, dbp in enumerate(dbs):
        db = sqlite3.connect(dbp)
        db.execute("create table session_v2 (id, parent_id, title, directory, time_updated)")
        db.execute("create table session_message (id, session_id, type, time_updated, data, seq)")
        db.execute("insert into session_v2 values ('s', null, ?, '/x', ?), ('k', 's', 'kid', '/x', 2)",
                   (f"db{n}", time.time() * 1000 if n else 1))
        db.execute("insert into session_message values ('m', 's', 'user', 5, ?, 1)", (json.dumps({"text": f"hi{n}"}),))
        db.execute("insert into session_message values ('i', 's', 'idle', 5, '{}', 2)")
        db.commit()
        db.close()
    server.OPENCODE = dbs
    assert [(s["id"], s["parent"], s["title"]) for s in server.opencode_sessions()] == [
        ("0/s", None, "db0"), ("0/k", "0/s", "kid"), ("1/s", None, "db1"), ("1/k", "1/s", "kid")]
    # ended on an idle message and recently updated; the same session a long time ago doesn't count
    assert [s["waiting"] for s in server.opencode_sessions()] == [False, False, True, False]
    events, cursor = server.opencode_tail("1/s", 0)
    assert events[0]["text"] == "hi1" and cursor == 5
    for bad in ["2/s", "s", "-1/s"]:
        try:
            server.opencode_tail(bad, 0)
            raise AssertionError(f"bad id allowed: {bad!r}")
        except ValueError:
            pass

    # config: no file means the built-in defaults
    os.environ.pop("ATV_CONFIG", None)
    os.environ["XDG_CONFIG_HOME"] = str(tmp / "xdg")
    assert server.load_config() == (None, {
        "claude": [Path.home() / ".claude/projects"],
        "opencode": [Path.home() / ".local/share/opencode/opencode.db"]}, {})

    # overrides, with ~ and $VAR expanded; a missing path is skipped; an empty list turns a scaffold off
    cfg = tmp / "xdg/atv/config.json"
    cfg.parent.mkdir(parents=True)
    os.environ["ATV_TEST_ROOT"] = str(tmp)
    cfg.write_text(json.dumps({"port": 9, "scaffolds": {
        "claude": {"paths": ["$ATV_TEST_ROOT/r0", "$ATV_TEST_ROOT/nope", "~"]}, "opencode": {"paths": []}},
        "projects": {"$ATV_TEST_ROOT/w/": {"color": "#0ff"}, "~/x": {}}}))
    want = (9, {"claude": [r0, Path.home()], "opencode": []}, {str(tmp / "w"): "#0ff"})
    assert server.load_config() == want                      # found via XDG_CONFIG_HOME
    os.environ["XDG_CONFIG_HOME"] = str(tmp / "elsewhere")
    assert server.load_config(str(cfg)) == want              # --config
    os.environ["ATV_CONFIG"] = str(cfg)
    assert server.load_config() == want                      # ATV_CONFIG
    server.OPENCODE = want[1]["opencode"]
    assert server.opencode_sessions() == []

    # a configured project colour reaches the session list; other projects get none
    server.COLORS = {"/w": "#0ff"}
    server.claude_sessions = lambda: [{"id": "a", "parent": None, "updated": 1, "cwd": "/w/"},
                                      {"id": "b", "parent": None, "updated": 0, "cwd": "/w/sub"}]
    assert [s["color"] for s in server.sessions()] == ["#0ff", None]

    # a partial file keeps the other defaults
    cfg.write_text(json.dumps({"scaffolds": {"opencode": {"paths": [str(dbs[0])]}}}))
    assert server.load_config() == (None, {"claude": [Path.home() / ".claude/projects"], "opencode": [dbs[0]]}, {})

    # startup errors name the file
    for text in ["{nope", json.dumps({"scaffolds": {"cursor": {"paths": []}}}),
                 json.dumps({"projects": {"/w": {"color": 3}}}), None]:
        if text is None:
            cfg.unlink()  # an explicitly named file has to exist
        else:
            cfg.write_text(text)
        try:
            server.load_config()
            raise AssertionError(f"bad config accepted: {text!r}")
        except SystemExit as e:
            assert str(cfg) in str(e), e
    del os.environ["ATV_CONFIG"]

oc = server.opencode_events("m1", "assistant", {"time": {"created": 1}, "content": [
    {"type": "reasoning", "text": ""},
    {"type": "tool", "name": "read", "state": {"status": "error", "input": {}, "error": "nope"}},
    {"type": "tool", "name": "read", "state": {"status": "running", "input": {}}}]})
assert [(e["output"], e["error"]) for e in oc] == [("nope", True), (None, False)], oc
print("ok")
