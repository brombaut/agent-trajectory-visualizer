# ATV — Agent Trajectory Visualizer

A local, read-only viewer for Claude Code and opencode sessions, used in a browser tab or as its own
desktop window. It is deliberately
quick and dirty: a personal tool, not a product. Keep it small.

## Commands

```sh
python3 server.py [port] [--port N] [--config FILE]   # serve on http://127.0.0.1:8765 (default port)
python3 test_server.py                                # self-check; prints "ok"
npm start [-- --config FILE]                          # desktop window (Electron); `npm install` once first
```

`index.html` is read from disk on every request, so frontend edits only need a page reload.
Changes to `server.py` need a server restart, and the desktop app starts its own
server, so there that means restarting the app. Changes to `electron/main.js` also need an app restart.

## Layout

- `server.py`: finds sessions, normalizes both formats into one event shape, serves JSON.
- `index.html`: the entire frontend (CSS and JS inline).
- `test_server.py`: plain-assert checks for the parsers and the tailing logic.
- `assets/`: README images, and `icon.png` (the app icon, cropped from the banner). The `*-dark.png` files are the
  originals from the old dark theme, kept but unused.
- `README.md`: kept short on purpose, just what ATV is and how to run it. Reference detail goes in
  `CONFIG.md` (the config file) and `electron/README.md` (desktop app notes: sandbox fix, launcher
  entry), which the README links to.
- `electron/main.js`: the desktop shell. Spawns `server.py --port 0`, reads the port from its
  `listening on` line, opens a window on it, and kills the server on quit. No preload, no bundler.
- `package.json`, `package-lock.json`: only there to pin Electron and provide `npm start`.
- `atv.desktop`: launcher entry template; `@REPO@` is replaced on install (see `electron/README.md`).

## Ground rules

- Work directly on the `main` branch. Only create or switch to another branch when explicitly asked to.
- Python standard library only, and no frontend dependencies or build step. Don't add either without asking.
  The one exception is `electron` as a devDependency for the desktop shell; nothing else goes in `package.json`.
- The Electron window keeps `contextIsolation`, no `nodeIntegration`, no preload, the Chromium sandbox,
  and navigation limited to the local server origin. Don't relax these.
- Don't split files or add abstractions for their own sake; one server file and one HTML file is the design.
- Never write to the session stores. Everything under `~/.claude` and the opencode database is opened read-only.
- The server must stay bound to `127.0.0.1` and keep the `Host` header check; sessions contain secrets.
- Session ids from the browser are untrusted. An id is `<root index>/<rest>`: the index must name a configured
  root, and for Claude Code the rest is a file path that must resolve inside that root (`split_root`, `claude_tail`).
- Build DOM with `textContent` (the `el()` helper), never `innerHTML`; session content is arbitrary text.

## How it works

Every record becomes one or more events:

```
{id, kind: user|assistant|thinking|tool|result|system|meta, ts, text?, name?, input?, output?, error?, extra?}
```

The browser polls `/api/events?source=&id=&cursor=` and upserts events by `id`, so re-sending an
event is harmless and an updated event replaces its row in place.
Each open session is a pane (a column) with its own cursor and event map, polled separately; the URL
hash lists the open session keys, each URI-encoded and joined with `,`.

- **Claude Code** (default root `~/.claude/projects`; `<project>/<session>.jsonl`, subagents in `<session>/subagents/`):
  append-only, so the cursor is a byte offset and a half-written last line is left for the next poll.
  Tool results are separate records; they are sent as `result` events and folded into the matching
  `tool` event in the browser, keyed on the tool-use id. A message the user sends while the agent is
  working is logged only as a `queued_command` attachment, not a `user` record; those with a human
  origin become `user` events, while task notifications of the same type stay `meta`.
- **opencode** (default `~/.local/share/opencode/opencode.db`, tables `session_v2` and `session_message`):
  rows are rewritten while a message streams, so the cursor is the max `time_updated` and the query
  uses `>=`; the browser drops unchanged events.
- **Roots and ids**: each scaffold has a list of roots (`CLAUDE`, `OPENCODE`), set from the config file by
  `load_config` (lookup order and rules are in `CONFIG.md`). Session ids are `<root index>/<rest>`, where the
  index counts only the roots that exist. The browser treats ids as opaque.
- **Startup line**: after binding, the server prints `listening on http://127.0.0.1:<port>` and flushes.
  `electron/main.js` parses the port from it, so keep the wording and the flush.
- `meta` events (system reminders, hook output, attachments) are sent but hidden unless the meta box is ticked.
- A project is a session's working directory; subagents inherit their parent's project. Grouping is done in the browser.
- **Project colours**: hashed from the project path in the browser (`hue`), unless the config file's `projects`
  gives that directory a `color`; `sessions()` then sends it as `color` and the browser uses it as is (`paint`).
- **Waiting for the human** (`waiting` on a session, shown as a bell): for Claude Code it comes from the
  `<pid>.json` status files in the `sessions` dir beside each projects root (`claude_live`), counting `idle`
  and `waiting` for processes still running; the transcript can't tell idle from closed. For opencode it is a
  top-level session whose last message is `idle`, updated within a day.
- **Live** (`live` on a session; the "Open live" button opens these and closes the other panes): for Claude Code,
  a top-level session whose process is still running per `claude_live` (after `/clear` the status file names the
  new session, so the old one drops out). For opencode, a top-level session that is waiting or was updated in the
  last 5 minutes. The button respects the project filter and search box.

## Checking changes

- Run `python3 test_server.py` after touching `server.py`, and add an assert there for new parsing logic.
- Both formats are undocumented and change between tool versions. When adding support for a record
  type, inspect real data first rather than guessing the shape.
- Headless screenshots are taken before the page's fetches finish, so an empty events pane in one
  is not evidence of a bug.
- `test_server.py` does not cover the desktop app. To check it, run `npm start -- --remote-debugging-port=9333`
  and inspect the page over the DevTools protocol; closing the window from inside the page with `window.close()`
  did not save the window bounds when tested, so quit with SIGTERM or the window's close button to check that.
- If Electron aborts at startup with a SUID sandbox error, apply the fix in `electron/README.md`; do not add
  `--no-sandbox`.
