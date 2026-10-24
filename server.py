#!/usr/bin/env python3
"""Agent trajectory visualizer for Claude Code, opencode and Codex sessions.

Run:  python3 server.py [port] [--port N] [--config FILE]   ->  http://127.0.0.1:8765

Every event sent to the browser is normalized to:
  {id, kind: user|assistant|thinking|tool|result|system|meta, ts, text?, name?, input?, output?, error?, extra?}
"""
import argparse
import json
import os
import re
import sqlite3
import sys
import time
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

DEFAULTS = {"claude": ["~/.claude/projects"], "opencode": ["~/.local/share/opencode/opencode.db"],
            "codex": ["~/.codex/sessions"]}
CLAUDE = [Path(p).expanduser() for p in DEFAULTS["claude"]]      # roots; session ids are "<root index>/<rest>"
OPENCODE = [Path(p).expanduser() for p in DEFAULTS["opencode"]]
CODEX = [Path(p).expanduser() for p in DEFAULTS["codex"]]
COLORS = {}  # project directory -> CSS colour from the config; other projects get a hashed one in the browser
HERE = Path(__file__).parent
LIVE_MS = 30 * 60e3  # a session left idle longer than this isn't live, even if its process is still open


def load_config(path=None):
    """-> (port or None, {scaffold: [Path, ...]}, {project dir: colour}). A problem with the file exits, naming it."""
    explicit = path or os.environ.get("ATV_CONFIG")
    f = Path(explicit or Path(os.environ.get("XDG_CONFIG_HOME") or "~/.config") / "atv/config.json").expanduser()
    port, roots, colors = None, {k: [Path(p).expanduser() for p in v] for k, v in DEFAULTS.items()}, {}
    if not explicit and not f.exists():
        return port, roots, colors
    try:
        cfg = json.loads(f.read_text())
        port = cfg.get("port")
        if port is not None and type(port) is not int:
            raise ValueError("port must be a number")
        scaffolds = cfg.get("scaffolds", {})
        if unknown := sorted(set(scaffolds) - set(DEFAULTS)):
            raise ValueError(f"unknown scaffold {', '.join(unknown)} (known: {', '.join(DEFAULTS)})")
        for name, sc in scaffolds.items():
            if "paths" not in sc:
                continue
            if not isinstance(sc["paths"], list):
                raise ValueError(f"scaffolds.{name}.paths must be a list")
            roots[name] = []
            for raw in sc["paths"]:
                p = Path(os.path.expandvars(raw)).expanduser()
                if p.exists():
                    roots[name].append(p)
                else:
                    print(f"warning: {f}: {name} path {p} does not exist, skipping", file=sys.stderr)
        for raw, pr in cfg.get("projects", {}).items():
            if "color" not in pr:
                continue
            if not isinstance(pr["color"], str):
                raise ValueError(f"projects.{raw}.color must be a string")
            colors[str(Path(os.path.expandvars(raw)).expanduser())] = pr["color"]  # Path drops a trailing slash
    except (OSError, ValueError, TypeError, AttributeError) as e:  # the last two: wrong JSON shape
        raise SystemExit(f"config {f}: {e}")
    return port, roots, colors


def split_root(sid, roots):
    """'<root index>/<rest>' -> (root, rest). Ids come from the browser, so a bad index is a ValueError."""
    i, _, rest = sid.partition("/")
    if not (i.isascii() and i.isdigit()) or int(i) >= len(roots):
        raise ValueError("bad session id")
    return roots[int(i)], rest


def text_of(content):
    """Flatten content blocks to text; non-text blocks (images, files) become placeholders."""
    if isinstance(content, str):
        return content
    out = []
    for b in content or []:
        if isinstance(b, dict):
            out.append(b.get("text", "") if b.get("type") == "text" else f"[{b.get('type')}]")
        else:
            out.append(str(b))
    return "\n".join(out)


# ---------- Claude Code: one JSONL file per session, append-only ----------

_info_cache = {}  # path -> ((mtime, size), info)


def claude_info(p, i=0):
    """i is the index of the root in CLAUDE that p was found under."""
    st = p.stat()
    key = (st.st_mtime, st.st_size)
    if p in _info_cache and _info_cache[p][0] == key:
        return _info_cache[p][1]
    title = prompt = cwd = replied = None
    with open(p, errors="replace") as f:
        for line in f:
            # Parse everything until we have cwd + first prompt, then only title candidates.
            if cwd and prompt and "ai-title" not in line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("type") == "ai-title":
                title = d.get("aiTitle")
            cwd = cwd or d.get("cwd")
            replied = replied or d.get("type") == "assistant"
            if not prompt and d.get("type") == "user" and not d.get("isMeta"):
                p_ = " ".join(text_of((d.get("message") or {}).get("content")).split())
                if not p_.startswith(("<", "[")):  # slash-command wrappers, tool results, interrupts
                    prompt = p_
    rel = p.relative_to(CLAUDE[i]).with_suffix("")
    parent = None
    if rel.parent.name == "subagents":
        parent = f"{i}/{rel.parent.parent}"
        meta = p.with_suffix(".meta.json")
        if meta.exists():
            m = json.loads(meta.read_text())
            title = f"{m.get('agentType', 'agent')}: {m.get('description', '')}"
    info = {
        "source": "claude", "id": f"{i}/{rel}", "parent": parent, "cwd": cwd or "",
        "title": title or (prompt or "(untitled)")[:120], "updated": int(st.st_mtime * 1000),
        # /clear starts a new file holding only the command itself; it stays a stub until a prompt arrives
        "empty": not (title or prompt or replied),
    }
    _info_cache[p] = (key, info)
    return info


def claude_live():
    """Session id -> status file (status: busy, idle, waiting, shell, ...) of the Claude Code processes still running.

    Each one keeps a <pid>.json in the sessions dir beside the projects dir. The transcript alone can't
    tell an idle session from a closed one, and never shows a pending permission prompt."""
    out = {}
    for root in CLAUDE:
        for f in (root.parent / "sessions").glob("*.json"):
            try:
                d = json.loads(f.read_text())
                if os.name == "posix":
                    os.kill(d["pid"], 0)  # signal 0 sends nothing; it raises if the process is gone
                out[d["sessionId"]] = d
            except (OSError, ValueError, KeyError, TypeError):  # stale or half-written file
                pass
    return out


def claude_sessions():
    out, live, now = [], claude_live(), time.time() * 1000
    for i, root in enumerate(CLAUDE):
        for p in [*root.glob("*/*.jsonl"), *root.glob("*/*/subagents/*.jsonl")]:
            try:
                # idle: turn finished; waiting: blocked on a permission prompt or a question
                info, st = claude_info(p, i), live.get(p.stem, {})
                if not info["empty"]:
                    # live: its process is still running (after /clear the status file names the new session) and
                    # it is working, or changed status recently. Not the transcript mtime: background writes bump it.
                    out.append({**info, "waiting": st.get("status") in ("idle", "waiting"),
                                "live": bool(st) and (st.get("status") in ("busy", "waiting")
                                                      or now - (st.get("statusUpdatedAt") or 0) < LIVE_MS)})
            except (OSError, ValueError) as e:
                print(f"skip {p}: {e}", file=sys.stderr)
    return out


def claude_events(d, n):
    """One JSONL record -> normalized events. n is a fallback unique key (byte offset)."""
    t, ts, uid = d.get("type"), d.get("timestamp"), d.get("uuid") or str(n)
    m = d.get("message") or {}

    def ev(i, kind, **kw):
        return {"id": f"{uid}:{i}", "kind": kind, "ts": ts, **kw}

    if t == "user":
        kind = "meta" if d.get("isMeta") else "user"
        c = m.get("content")
        if isinstance(c, str):
            return [ev(0, kind, text=c)]
        out = []
        for i, b in enumerate(c or []):
            if b.get("type") == "tool_result":
                # Folded into the matching tool call by the browser (matched on tool id).
                out.append(ev(i, "result", tool=b.get("tool_use_id"),
                              output=text_of(b.get("content")), error=bool(b.get("is_error"))))
            else:
                out.append(ev(i, kind, text=text_of([b])))
        return out
    if t == "assistant":
        extra = {k: m.get(k) for k in ("model", "stop_reason", "usage")}
        out = []
        for i, b in enumerate(m.get("content") or []):
            bt = b.get("type")
            if bt == "text":
                out.append(ev(i, "assistant", text=b.get("text", ""), extra=extra))
            elif bt == "thinking":
                if b.get("thinking"):  # usually redacted to an empty string + signature
                    out.append(ev(i, "thinking", text=b["thinking"]))
            elif bt == "tool_use":
                out.append({"id": b["id"], "kind": "tool", "ts": ts, "name": b.get("name"),
                            "input": b.get("input"), "output": None, "extra": extra})
            else:
                out.append(ev(i, "meta", name=bt, extra=b))
        return out
    if t == "system":
        return [ev(0, "system" if d.get("content") else "meta", name=d.get("subtype"),
                   text=d.get("content") or "", extra=d)]
    if t == "attachment":
        a = d.get("attachment") or {}
        # A message typed while the agent was working is logged only as this attachment, never as a
        # user record. Task notifications and subagent hand-backs share the type but aren't human.
        if a.get("type") == "queued_command" and (a.get("origin") or {}).get("kind") == "human":
            return [ev(0, "user", text=text_of(a.get("prompt")))]
        return [ev(0, "meta", name=a.get("type"), extra=a)]
    return []  # bookkeeping records (mode, file-history-*, last-prompt, ...)


def claude_tail(sid, offset):
    return jsonl_tail(CLAUDE, claude_events, sid, offset)


def jsonl_tail(roots, parse, sid, offset):
    root, rel = split_root(sid, roots)
    p = (root / (rel + ".jsonl")).resolve()
    if not p.is_relative_to(root.resolve()):  # must stay inside the root the id names
        raise ValueError("bad session id")
    events = []
    with open(p, "rb") as f:
        f.seek(offset)
        for line in f:
            if not line.endswith(b"\n"):
                break  # half-written line; pick it up on the next poll
            try:
                events += parse(json.loads(line), offset)
            except Exception as e:  # one odd record shouldn't blank the whole session
                events.append({"id": f"bad:{offset}", "kind": "meta", "ts": None,
                               "name": "unparsed", "text": f"{e!r}\n{line[:2000]!r}"})
            offset += len(line)
    return events, offset


# ---------- opencode: sqlite, rows are updated in place while streaming ----------

def oc_db(path):
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)


def opencode_sessions():
    out, now = [], time.time() * 1000
    for n, path in enumerate(OPENCODE):
        if not path.exists():
            continue
        try:
            with closing(oc_db(path)) as db:
                rows = db.execute(
                    "select id, parent_id, title, directory, time_updated, (select type from session_message"
                    " where session_id = s.id order by seq desc limit 1) from session_v2 s").fetchall()
        except sqlite3.Error as e:
            print(f"opencode {path}: {e}", file=sys.stderr)
            continue
        for i, par, title, cwd, upd, last in rows:
            # An idle message ends each turn. ponytail: nothing says whether opencode is still
            # running, so a day-old one counts as closed; use a process check if one turns up.
            waiting = last == "idle" and not par and upd > now - 86400e3
            out.append({"source": "opencode", "id": f"{n}/{i}", "parent": par and f"{n}/{par}",
                        "title": title or "(untitled)", "cwd": cwd or "", "updated": upd, "waiting": waiting,
                        "live": not par and upd > now - LIVE_MS})
    return out


def opencode_events(mid, typ, d):
    ts = (d.get("time") or {}).get("created")

    def ev(i, kind, **kw):
        return {"id": f"{mid}:{i}", "kind": kind, "ts": ts, **kw}

    if typ == "user":
        return [ev(0, "user", text=d.get("text", ""), extra={"files": d["files"]} if d.get("files") else None)]
    if typ == "assistant":
        extra = {k: d[k] for k in ("agent", "model", "finish", "cost", "tokens") if d.get(k) is not None}
        out = []
        for i, b in enumerate(d.get("content") or []):
            bt = b.get("type")
            if bt == "text":
                out.append(ev(i, "assistant", text=b.get("text", ""), extra=extra))
            elif bt == "reasoning":
                if b.get("text"):  # usually encrypted, so empty
                    out.append(ev(i, "thinking", text=b["text"]))
            elif bt == "tool":
                st, tm = b.get("state") or {}, b.get("time") or {}
                status, err = st.get("status"), st.get("error")
                if status == "error":
                    output = err if isinstance(err, str) else json.dumps(err, indent=2)
                else:
                    output = text_of(st.get("content")) if status == "completed" else None
                # the call's own start and end, not its message's: the browser times tool calls with them
                out.append(ev(i, "tool", ts=tm.get("created") or ts, end=tm.get("completed"), name=b.get("name"),
                              input=st.get("input"), output=output, error=status == "error", extra={**extra, "time": tm}))
            else:
                out.append(ev(i, "meta", name=bt, extra=b))
        if d.get("error"):
            out.append(ev("err", "system", name="error", error=True, text=json.dumps(d["error"], indent=2)))
        return out
    # system, synthetic, compaction, model-switched, idle
    return [ev(0, "meta" if typ == "idle" else "system", name=typ,
               text=d.get("text") or d.get("summary") or "", extra=d)]


def opencode_tail(sid, since):
    # >= so a row rewritten within the cursor's millisecond isn't missed; the browser dedupes.
    path, sid = split_root(sid, OPENCODE)
    with closing(oc_db(path)) as db:
        rows = db.execute(
            "select id, type, time_updated, data from session_message"
            " where session_id = ? and time_updated >= ? order by seq", (sid, since)).fetchall()
    events = []
    for mid, typ, upd, data in rows:
        since = max(since, upd)
        events += opencode_events(mid, typ, json.loads(data))
    return events, since


# ---------- Codex: one append-only JSONL "rollout" file per session, under YYYY/MM/DD/ ----------

def codex_info(p, i=0):
    """i is the index of the root in CODEX that p was found under."""
    st = p.stat()
    key = (st.st_mtime, st.st_size)
    if p in _info_cache and _info_cache[p][0] == key:
        return _info_cache[p][1]
    meta, prompt, done = {}, None, False
    with open(p, errors="replace") as f:
        for line in f:
            head = line[:200]  # each record starts {"timestamp", "ordinal", "type", "payload": {"type"
            if '"task_started"' in head or '"task_complete"' in head:
                done = '"task_complete"' in head  # the last one says whether a turn is still running
            elif not meta and '"session_meta"' in head:
                meta = json.loads(line)["payload"]
            elif not prompt and '"user.text"' in line and '"response_item"' in head:
                prompt = " ".join(codex_text(json.loads(line)["payload"]).split())
    info = {"source": "codex", "id": f"{i}/{p.relative_to(CODEX[i]).with_suffix('')}", "parent": None,
            "sid": meta.get("id"), "cwd": meta.get("cwd") or "", "title": (prompt or "(untitled)")[:120],
            "updated": int(st.st_mtime * 1000), "done": done}
    _info_cache[p] = (key, info)
    return info


def codex_sessions():
    out, now = [], time.time() * 1000
    for i, root in enumerate(CODEX):
        titles = {}  # thread id -> generated title; a later line for the same id wins
        try:
            with open(root.parent / "session_index.jsonl", errors="replace") as f:
                for line in f:
                    try:
                        d = json.loads(line)
                        titles[d["id"]] = d["thread_name"]
                    except (ValueError, KeyError, TypeError):
                        pass
        except OSError:
            pass
        for p in root.glob("**/rollout-*.jsonl"):
            try:
                info = codex_info(p, i)
            except (OSError, ValueError, KeyError) as e:
                print(f"skip {p}: {e}", file=sys.stderr)
                continue
            # ponytail: like opencode, nothing here says whether Codex is still running, so a finished
            # turn under a day old counts as waiting; ~/.codex/thread-writer-locks may hold a process check.
            out.append({**info, "title": titles.get(info["sid"]) or info["title"],
                        "waiting": info["done"] and info["updated"] > now - 86400e3,
                        "live": info["updated"] > now - LIVE_MS})
    return out


def codex_text(m):
    return "\n".join(c.get("text", "") if "text" in c else f"[{c.get('type')}]" for c in m.get("content") or [])


def js_str(s):
    """Body of a double-quoted JS string literal -> its value; JS allows \\' where JSON doesn't."""
    try:
        return json.loads(f'"{s}"'.replace("\\'", "'"))
    except ValueError:
        return s


def codex_events(d, n):
    """One rollout record -> normalized events. Only response_item records and token counts are used: the
    other event_msg records repeat the same messages and commands, and the rest is settings."""
    p, ts = d.get("payload") or {}, d.get("timestamp")
    if d.get("type") == "event_msg" and p.get("type") == "token_count" and p.get("info"):
        return [{"id": str(n), "kind": "meta", "ts": ts, "name": "token_count", "extra": p["info"]}]  # context size
    if d.get("type") != "response_item":
        return []
    t, uid = p.get("type"), p.get("id") or str(n)
    if t == "message":
        role = p.get("role")
        kinds = (p.get("internal_chat_message_metadata_passthrough") or {}).get("content_item_kinds")
        if role == "assistant":
            return [{"id": uid, "kind": "assistant", "ts": ts, "text": codex_text(p), "extra": {"phase": p.get("phase")}}]
        # injected AGENTS.md, environment context and shell-command echoes are tagged with other kinds
        human = role == "user" and "user.text" in (kinds or ["user.text"])
        return [{"id": uid, "kind": "user" if human else "meta", "ts": ts, "name": role,
                 "text": codex_text(p), "extra": {"kinds": kinds}}]
    if t == "reasoning":
        text = "\n".join(s.get("text", "") for s in p.get("summary") or [])
        return [{"id": uid, "kind": "thinking", "ts": ts, "text": text}] if text else []  # usually encrypted only
    if t == "custom_tool_call":
        script = p.get("input") or ""
        # the exec tool runs a script calling tools.exec_command({cmd:"..."}) or applying a patch;
        # pull the commands or patched files out for the session view
        cmds = [js_str(c) for c in re.findall(r'\bcmd:\s*"((?:[^"\\]|\\.)*)"', script)]
        files = re.findall(r"\*\*\* (?:Add|Update|Delete) File: (.+?)(?:\\n|\n|\"|$)", script)
        inp = ({"command": "\n".join(cmds)} if cmds else {"path": "\n".join(files)} if files else {}) | {"script": script}
        return [{"id": p.get("call_id") or uid, "kind": "tool", "ts": ts, "name": p.get("name"), "input": inp, "output": None}]
    if t == "custom_tool_call_output":
        out = p.get("output")
        out = "\n".join(c.get("text", "") for c in out) if isinstance(out, list) else str(out)
        return [{"id": uid, "kind": "result", "ts": ts, "tool": p.get("call_id"), "output": out, "error": False}]
    return [{"id": uid, "kind": "meta", "ts": ts, "name": t, "extra": p}]


def codex_tail(sid, offset):
    return jsonl_tail(CODEX, codex_events, sid, offset)


# ---------- http ----------

def sessions():
    """Newest first, with subagent/child sessions listed directly under their parent."""
    all_ = claude_sessions() + opencode_sessions() + codex_sessions()
    ids = {s["id"] for s in all_}
    kids = {}
    for s in all_:
        if s["parent"] in ids:
            kids.setdefault(s["parent"], []).append(s)
    out = []

    def add(s, depth):
        out.append({**s, "depth": depth, "color": COLORS.get(s["cwd"].rstrip("/") or "/")})
        for k in sorted(kids.get(s["id"], []), key=lambda k: k["updated"]):
            add(k, depth + 1)

    for s in sorted(all_, key=lambda s: -s["updated"]):
        if s["parent"] not in ids:
            add(s, 0)
    return out


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        # Sessions hold secrets; refuse anything not addressed to localhost (DNS rebinding).
        if self.headers.get("Host", "").rsplit(":", 1)[0] not in ("127.0.0.1", "localhost"):
            return self.send_error(403)
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        ctype = "application/json"
        try:
            if u.path == "/":
                body, ctype = (HERE / "index.html").read_bytes(), "text/html; charset=utf-8"
            elif u.path == "/api/sessions":
                body = json.dumps(sessions()).encode()
            elif u.path == "/api/events":
                tail = {"claude": claude_tail, "opencode": opencode_tail, "codex": codex_tail}[q["source"]]
                events, cursor = tail(q["id"], int(q.get("cursor", 0)))
                body = json.dumps({"events": events, "cursor": cursor}).encode()
            else:
                return self.send_error(404)
        except (KeyError, ValueError, OSError, sqlite3.Error) as e:
            return self.send_error(400, repr(e))
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Agent trajectory visualizer")
    ap.add_argument("port_arg", nargs="?", type=int, metavar="port", help="same as --port")
    ap.add_argument("--port", type=int, help="port to listen on; 0 picks a free one (default 8765)")
    ap.add_argument("--config", help="config file (default: $ATV_CONFIG, then ~/.config/atv/config.json)")
    args = ap.parse_args()
    cfg_port, roots, COLORS = load_config(args.config)
    CLAUDE, OPENCODE, CODEX = roots["claude"], roots["opencode"], roots["codex"]
    port = next(p for p in (args.port, args.port_arg, cfg_port, 8765) if p is not None)
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    # The Electron wrapper parses this line from a pipe: keep the wording and the flush.
    print(f"listening on http://127.0.0.1:{srv.server_port}", flush=True)
    srv.serve_forever()
