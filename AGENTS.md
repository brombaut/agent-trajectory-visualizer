# ATV — Agent Trajectory Visualizer

A local, read-only browser viewer for Claude Code and opencode sessions. It is deliberately
quick and dirty: a personal tool, not a product. Keep it small.

## Commands

```sh
python3 server.py [port]   # serve on http://127.0.0.1:8765 (default port)
python3 test_server.py     # self-check; prints "ok"
npm start                  # desktop window (Electron); run `npm install` once first
```

`index.html` is read from disk on every request, so frontend edits only need a browser refresh.
Changes to `server.py` need a server restart.

## Layout

- `server.py`: finds sessions, normalizes both formats into one event shape, serves JSON.
- `index.html`: the entire frontend (CSS and JS inline).
- `test_server.py`: plain-assert checks for the parsers and the tailing logic.
- `assets/`: README images, and `icon.png` (the app icon, cropped from the banner).
- `CONFIG.md`: the config file reference, kept out of the main README.
- `electron/README.md`: desktop app notes kept out of the main README (sandbox fix, launcher entry).
- `electron/main.js`: the desktop shell. Spawns `server.py --port 0`, reads the port from its
  `listening on` line, opens a window on it, and kills the server on quit. No preload, no bundler.
- `package.json`, `package-lock.json`: only there to pin Electron and provide `npm start`.
- `atv.desktop`: launcher entry template; `@REPO@` is replaced on install (see `electron/README.md`).

## Ground rules

- Python standard library only, and no frontend dependencies or build step. Don't add either without asking.
  The one exception is `electron` as a devDependency for the desktop shell; nothing else goes in `package.json`.
- The Electron window keeps `contextIsolation`, no `nodeIntegration`, no preload, the Chromium sandbox,
  and navigation limited to the local server origin. Don't relax these.
- Don't split files or add abstractions for their own sake; one server file and one HTML file is the design.
- Never write to the session stores. Everything under `~/.claude` and the opencode database is opened read-only.
- The server must stay bound to `127.0.0.1` and keep the `Host` header check; sessions contain secrets.
- Session ids from the browser are untrusted: Claude Code ids are file paths and must resolve inside `~/.claude/projects`.
- Build DOM with `textContent` (the `el()` helper), never `innerHTML`; session content is arbitrary text.

## How it works

Every record becomes one or more events:

```
{id, kind: user|assistant|thinking|tool|result|system|meta, ts, text?, name?, input?, output?, error?, extra?}
```

The browser polls `/api/events?source=&id=&cursor=` and upserts events by `id`, so re-sending an
event is harmless and an updated event replaces its row in place.

- **Claude Code** (`~/.claude/projects/<project>/<session>.jsonl`, subagents in `<session>/subagents/`):
  append-only, so the cursor is a byte offset and a half-written last line is left for the next poll.
  Tool results are separate records; they are sent as `result` events and folded into the matching
  `tool` event in the browser, keyed on the tool-use id.
- **opencode** (`~/.local/share/opencode/opencode.db`, tables `session_v2` and `session_message`):
  rows are rewritten while a message streams, so the cursor is the max `time_updated` and the query
  uses `>=`; the browser drops unchanged events.
- `meta` events (system reminders, hook output, attachments) are sent but hidden unless the meta box is ticked.
- A project is a session's working directory; subagents inherit their parent's project. Grouping is done in the browser.

## Checking changes

- Run `python3 test_server.py` after touching `server.py`, and add an assert there for new parsing logic.
- Both formats are undocumented and change between tool versions. When adding support for a record
  type, inspect real data first rather than guessing the shape.
- Headless screenshots are taken before the page's fetches finish, so an empty events pane in one
  is not evidence of a bug.
