#!/usr/bin/env python3
"""Agent trajectory visualizer for Claude Code + opencode sessions.

Run:  python3 server.py [port] [--port N] [--config FILE]   ->  http://127.0.0.1:8765

Every event sent to the browser is normalized to:
  {id, kind: user|assistant|thinking|tool|result|system|meta, ts, text?, name?, input?, output?, error?, extra?}
"""
import argparse
import json
import os
import sqlite3
import sys
import time
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

DEFAULTS = {"claude": ["~/.claude/projects"], "opencode": ["~/.local/share/opencode/opencode.db"]}
CLAUDE = [Path(p).expanduser() for p in DEFAULTS["claude"]]      # roots; session ids are "<root index>/<rest>"
OPENCODE = [Path(p).expanduser() for p in DEFAULTS["opencode"]]
HERE = Path(__file__).parent


def load_config(path=None):
    """-> (port or None, {scaffold: [Path, ...]}). A problem with the file exits, naming it."""
    explicit = path or os.environ.get("ATV_CONFIG")
    f = Path(explicit or Path(os.environ.get("XDG_CONFIG_HOME") or "~/.config") / "atv/config.json").expanduser()
    port, roots = None, {k: [Path(p).expanduser() for p in v] for k, v in DEFAULTS.items()}
    if not explicit and not f.exists():
        return port, roots
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
    except (OSError, ValueError, TypeError, AttributeError) as e:  # the last two: wrong JSON shape
        raise SystemExit(f"config {f}: {e}")
    return port, roots


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
    title = prompt = cwd = None
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
    }
    _info_cache[p] = (key, info)
    return info


def claude_live():
    """Session id -> status (busy, idle, waiting, ...) of the Claude Code processes still running.

    Each one keeps a <pid>.json in the sessions dir beside the projects dir. The transcript alone can't
    tell an idle session from a closed one, and never shows a pending permission prompt."""
    out = {}
    for root in CLAUDE:
        for f in (root.parent / "sessions").glob("*.json"):
            try:
                d = json.loads(f.read_text())
                if os.name == "posix":
                    os.kill(d["pid"], 0)  # signal 0 sends nothing; it raises if the process is gone
                out[d["sessionId"]] = d.get("status")
            except (OSError, ValueError, KeyError, TypeError):  # stale or half-written file
                pass
    return out


def claude_sessions():
    out, live = [], claude_live()
    for i, root in enumerate(CLAUDE):
        for p in [*root.glob("*/*.jsonl"), *root.glob("*/*/subagents/*.jsonl")]:
            try:
                # idle: turn finished; waiting: blocked on a permission prompt or a question
                out.append({**claude_info(p, i), "waiting": live.get(p.stem) in ("idle", "waiting")})
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
    root, rel = split_root(sid, CLAUDE)
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
                events += claude_events(json.loads(line), offset)
            except Exception as e:  # one odd record shouldn't blank the whole session
                events.append({"id": f"bad:{offset}", "kind": "meta", "ts": None,
                               "name": "unparsed", "text": f"{e!r}\n{line[:2000]!r}"})
            offset += len(line)
    return events, offset


# ---------- opencode: sqlite, rows are updated in place while streaming ----------

def oc_db(path):
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)


def opencode_sessions():
    out = []
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
        out += [{"source": "opencode", "id": f"{n}/{i}", "parent": par and f"{n}/{par}",
                 "title": title or "(untitled)", "cwd": cwd or "", "updated": upd,
                 # An idle message ends each turn. ponytail: nothing says whether opencode is still
                 # running, so a day-old one counts as closed; use a process check if one turns up.
                 "waiting": last == "idle" and not par and upd > time.time() * 1000 - 86400e3}
                for i, par, title, cwd, upd, last in rows]
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
                st = b.get("state") or {}
                status, err = st.get("status"), st.get("error")
                if status == "error":
                    output = err if isinstance(err, str) else json.dumps(err, indent=2)
                else:
                    output = text_of(st.get("content")) if status == "completed" else None
                out.append(ev(i, "tool", name=b.get("name"), input=st.get("input"), output=output,
                              error=status == "error", extra={**extra, "time": b.get("time")}))
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


# ---------- http ----------

def sessions():
    """Newest first, with subagent/child sessions listed directly under their parent."""
    all_ = claude_sessions() + opencode_sessions()
    ids = {s["id"] for s in all_}
    kids = {}
    for s in all_:
        if s["parent"] in ids:
            kids.setdefault(s["parent"], []).append(s)
    out = []

    def add(s, depth):
        out.append({**s, "depth": depth})
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
                tail = {"claude": claude_tail, "opencode": opencode_tail}[q["source"]]
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
    cfg_port, roots = load_config(args.config)
    CLAUDE, OPENCODE = roots["claude"], roots["opencode"]
    port = next(p for p in (args.port, args.port_arg, cfg_port, 8765) if p is not None)
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    # The Electron wrapper parses this line from a pipe: keep the wording and the flush.
    print(f"listening on http://127.0.0.1:{srv.server_port}", flush=True)
    srv.serve_forever()
