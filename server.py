#!/usr/bin/env python3
"""Agent trajectory visualizer for Claude Code + opencode sessions.

Run:  python3 server.py [port]   ->  http://127.0.0.1:8765

Every event sent to the browser is normalized to:
  {id, kind: user|assistant|thinking|tool|result|system|meta, ts, text?, name?, input?, output?, error?, extra?}
"""
import json
import sqlite3
import sys
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

CLAUDE = Path.home() / ".claude/projects"
OPENCODE = Path.home() / ".local/share/opencode/opencode.db"
HERE = Path(__file__).parent


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


def claude_info(p):
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
    rel = p.relative_to(CLAUDE).with_suffix("")
    parent = None
    if rel.parent.name == "subagents":
        parent = str(rel.parent.parent)
        meta = p.with_suffix(".meta.json")
        if meta.exists():
            m = json.loads(meta.read_text())
            title = f"{m.get('agentType', 'agent')}: {m.get('description', '')}"
    info = {
        "source": "claude", "id": str(rel), "parent": parent, "cwd": cwd or "",
        "title": title or (prompt or "(untitled)")[:120], "updated": int(st.st_mtime * 1000),
    }
    _info_cache[p] = (key, info)
    return info


def claude_sessions():
    out = []
    for p in [*CLAUDE.glob("*/*.jsonl"), *CLAUDE.glob("*/*/subagents/*.jsonl")]:
        try:
            out.append(claude_info(p))
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
        return [ev(0, "meta", name=a.get("type"), extra=a)]
    return []  # bookkeeping records (mode, file-history-*, last-prompt, ...)


def claude_tail(sid, offset):
    p = (CLAUDE / (sid + ".jsonl")).resolve()
    if not p.is_relative_to(CLAUDE.resolve()):
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

def oc_db():
    return sqlite3.connect(f"file:{OPENCODE}?mode=ro", uri=True)


def opencode_sessions():
    if not OPENCODE.exists():
        return []
    try:
        with closing(oc_db()) as db:
            rows = db.execute(
                "select id, parent_id, title, directory, time_updated from session_v2").fetchall()
    except sqlite3.Error as e:
        print(f"opencode: {e}", file=sys.stderr)
        return []
    return [{"source": "opencode", "id": i, "parent": par, "title": title or "(untitled)",
             "cwd": cwd or "", "updated": upd} for i, par, title, cwd, upd in rows]


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
    with closing(oc_db()) as db:
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
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    print(f"http://127.0.0.1:{port}")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
