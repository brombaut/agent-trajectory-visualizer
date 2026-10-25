# ATV — Agent Trajectory Visualizer

A local, read-only viewer for Claude Code, opencode and Codex sessions, used in a browser tab or as its own
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

- `server.py`: finds sessions, normalizes all three formats into one event shape, serves JSON.
- `index.html`: the entire frontend (CSS and JS inline).
- `test_server.py`: plain-assert checks for the parsers and the tailing logic.
- `assets/`: README images (`screenshot.png` is of made-up sessions, never real ones), and `icon.png` (the app icon, cropped from the banner). The `*-dark.png` files are the
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
{id, kind: user|assistant|thinking|tool|result|system|meta, ts, text?, name?, input?, output?, error?, end?, extra?}
```

`end` is when a tool call's result came: set by the server for opencode, and by the browser when it folds a
`result` into its call for the other two.

The browser polls `/api/events?source=&id=&cursor=` and upserts events by `id`, so re-sending an
event is harmless and an updated event replaces its row in place.
Each open session is a pane (a column) with its own cursor and event map, polled separately; the URL
hash lists the open session keys, each URI-encoded and joined with `,`. Focus (`focus()`, the ⤢ button) hides
the sidebar and the other panes with CSS; they keep polling, and their scroll positions are restored on return.
Focus is not in the hash.

- **Claude Code** (default root `~/.claude/projects`; `<project>/<session>.jsonl`, subagents in `<session>/subagents/`):
  append-only, so the cursor is a byte offset and a half-written last line is left for the next poll.
  Tool results are separate records; they are sent as `result` events and folded into the matching
  `tool` event in the browser, keyed on the tool-use id. A message the user sends while the agent is
  working is logged only as a `queued_command` attachment, not a `user` record; those with a human
  origin become `user` events, while task notifications of the same type stay `meta`.
- **opencode** (default `~/.local/share/opencode/opencode.db`, tables `session_v2` and `session_message`):
  rows are rewritten while a message streams, so the cursor is the max `time_updated` and the query
  uses `>=`; the browser drops unchanged events.
- **Codex** (default root `~/.codex/sessions`; `YYYY/MM/DD/rollout-<time>-<uuid>.jsonl`): append-only, tailed
  like Claude Code. Only `response_item` records and `token_count` ones (as `meta`, for the context size) become
  events; the other `event_msg` records repeat the same messages and commands. Injected context (AGENTS.md, environment, `developer` messages) is `meta`: a `user` message is only
  human if its `content_item_kinds` include `user.text`. Tool calls are `exec` JS scripts; the `cmd:` strings and
  patched file paths are pulled out as `command`/`path`. Reasoning is encrypted, so it shows only if a summary is
  present. Titles come from `session_index.jsonl` beside the root. No subagents seen yet.
- **Roots and ids**: each scaffold has a list of roots (`CLAUDE`, `OPENCODE`, `CODEX`), set from the config file by
  `load_config` (lookup order and rules are in `CONFIG.md`). Session ids are `<root index>/<rest>`, where the
  index counts only the roots that exist. The browser treats ids as opaque.
- **Startup line**: after binding, the server prints `listening on http://127.0.0.1:<port>` and flushes.
  `electron/main.js` parses the port from it, so keep the wording and the flush.
- `meta` events (system reminders, hook output, attachments) are sent but hidden unless the meta box is ticked.
- An expanded row's `details` section (`extra` as JSON) is hidden unless the details box is ticked (`show-details`),
  except in rows with nothing else to show, which is most `meta` rows (issue #16).
- Expanded assistant messages and thinking blocks are rendered as markdown by `md()`/`inline()`, a small hand-written
  subset (headings, lists, code, tables, quotes, bold, italic, http(s) links only) built with `el()` and text nodes.
  Their raw text is there too, in a `.raw` `<pre>` shown instead when the raw box is ticked (`show-raw`) (issues #18, #21).
- A pane follows the bottom if it is within 80px of it when it polls. Row bodies are built when first opened; a row
  opened in code (Expand all, an open row replaced in place) builds its body at once (`setOpen`), and the view
  settings go through `keepBottom`, so neither leaves a following pane short of the bottom (issue #19).
- **Where the time went** (issue #14): `walk()` turns a pane's events into segments, in the browser.
  Each gap between timestamps goes to what was pending when it began: a tool call (`AskUserQuestion` and
  `ExitPlanMode` count as you), else the model. After a turn ends (per format: `turnEnd`) the wait is yours if
  a user event ends it, background otherwise (async subagents end the parent's turn and wake it with a task
  notification). Gaps over 5 min are idle unless a tool call that later finished was running (a question left
  overnight is idle). `summary()` totals them in the line under each pane's header; `strip()` draws them under
  that as an SVG band along active time (idle cut to slivers), a lane per subagent, and the context size as a
  line labelled with its scale (the window if known, else the peak). Clicking a segment opens its row, clicking a
  lane opens that subagent's pane. A pane polls its subagents too (`p.kids`, events only, refetched when their
  `updated` changes); their timestamps split the parent's gaps, so waiting on them isn't idle. A container query
  hides the strip in panes under 500px wide.
- A project is a session's working directory; subagents inherit their parent's project. Grouping is done in the browser.
- **Project colours**: hashed from the project path in the browser (`hue`), unless the config file's `projects`
  gives that directory a `color`; `sessions()` then sends it as `color` and the browser uses it as is (`paint`).
- **Waiting for the human** (`waiting` on a session, shown as a bell): for Claude Code it comes from the
  `<pid>.json` status files in the `sessions` dir beside each projects root (`claude_live`), counting `idle`
  and `waiting` for processes still running; the transcript can't tell idle from closed. For opencode it is a
  top-level session whose last message is `idle`, updated within a day; for Codex, one whose last turn event is
  `task_complete`, updated within a day.
- **Live** (`live` on a session; the "Open live" button opens these and closes the other panes): for Claude Code,
  a top-level session whose process is still running per `claude_live` (after `/clear` the status file names the
  new session, so the old one drops out) and is `busy` or `waiting`, or changed status (`statusUpdatedAt`) within
  `LIVE_MS` (30 min). Not the transcript mtime, which background writes can bump. For opencode and Codex, a top-level session
  updated within `LIVE_MS`. The button respects the project filter and search box.

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
