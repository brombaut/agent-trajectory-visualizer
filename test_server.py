"""Run: python3 test_server.py"""
import json
import tempfile
from pathlib import Path

import server

with tempfile.TemporaryDirectory() as tmp:
    server.CLAUDE = Path(tmp)
    (Path(tmp) / "proj").mkdir()
    f = Path(tmp) / "proj/abc.jsonl"
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

    events, cursor = server.claude_tail("proj/abc", 0)
    assert [e["kind"] for e in events] == ["user", "assistant", "tool", "result"], events
    assert events[2]["id"] == events[3]["tool"] == "t1"
    assert events[3]["output"] == "boom\n[image]" and events[3]["error"]
    # the half-written last line is not consumed, and re-polling yields nothing new
    assert cursor == f.stat().st_size - len('{"type": "user", "uuid": "half')
    assert server.claude_tail("proj/abc", cursor) == ([], cursor)

    assert server.claude_info(f)["title"] == "hello" and server.claude_info(f)["cwd"] == "/x"

    try:
        server.claude_tail("../../etc/passwd", 0)
        raise AssertionError("path traversal allowed")
    except ValueError:
        pass

oc = server.opencode_events("m1", "assistant", {"time": {"created": 1}, "content": [
    {"type": "reasoning", "text": ""},
    {"type": "tool", "name": "read", "state": {"status": "error", "input": {}, "error": "nope"}},
    {"type": "tool", "name": "read", "state": {"status": "running", "input": {}}}]})
assert [(e["output"], e["error"]) for e in oc] == [("nope", True), (None, False)], oc
print("ok")
