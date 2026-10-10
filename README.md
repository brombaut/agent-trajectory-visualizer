# ATV — Agent Trajectory Visualizer

![A rider on an ATV climbing a trail made of user, tool and assistant turns](assets/atv-header.png)

A quick-and-dirty view for going off-road through your coding-agent sessions: every
project, every session, every turn, live as it happens. Open it in a browser tab or run it as its
own desktop window. It reads [Claude Code](https://claude.com/claude-code)
and [opencode](https://opencode.ai) sessions straight from where those tools already store them.

## Ride

There are two ways to run it. They show the same thing, and can run side by side.

### In the browser

```sh
python3 server.py        # then open http://127.0.0.1:8765
python3 server.py 9000   # or pick a port
```

Python 3 standard library only. Nothing to install, nothing to build. Stop it with Ctrl+C.

### As a desktop app

ATV in its own window, with its own launcher and alt-tab entry. Linux, and needs Node as well as Python 3.

```sh
npm install   # once; Electron is the only dependency
npm start     # starts its own server on a free port and opens the window
```

Closing the window quits the app and stops its server. Starting it again while it is running
focuses the existing window. Window size is remembered in `~/.config/ATV/window.json`; position is
too, except on Wayland, which does not let apps place their own windows.

**Sandbox error on first start.** On recent Ubuntu, Electron aborts with "The SUID sandbox helper
binary was found, but is not configured correctly", because the distro restricts unprivileged user
namespaces. Fix it as the message says, from this directory. It needs root, and needs redoing
whenever `npm install` replaces the Electron binary:

```sh
sudo chown root node_modules/electron/dist/chrome-sandbox && sudo chmod 4755 node_modules/electron/dist/chrome-sandbox
```

**Launcher entry (optional).** To get ATV in the application menu with its own icon, so it can be
started without a terminal, install `atv.desktop` with the path of this checkout filled in. Run
this from this directory, and again if the directory moves:

```sh
sed "s|@REPO@|$PWD|g" atv.desktop > ~/.local/share/applications/atv.desktop
```

## The terrain

- **Projects** on the far left, grouped by working directory. Picking one opens its latest session.
- **Sessions** next to it, newest first, with subagent sessions nested under their parent.
- **Trajectory** on the right: one line per user message, assistant reply and tool call.
  Click a line for the full text, tool inputs, output and token details, or hit **Expand all**.
- **Live**: the open session is tailed every 1.5s and follows the bottom unless you scroll up.
- **meta** shows the background records hidden by default: system reminders, hook output, attachments.

## Where it looks

| Tool        | Source                                  |
| ----------- | --------------------------------------- |
| Claude Code | `~/.claude/projects/**/*.jsonl`          |
| opencode    | `~/.local/share/opencode/opencode.db`    |

Everything is read-only, and the server only listens on localhost, since sessions tend to contain secrets.

## Configuration

Optional, and shared by both ways of running. To look somewhere else, or in several places, write
a JSON file. The first of these wins:

1. `--config <path>` (`python3 server.py --config <path>` or `npm start -- --config <path>`)
2. the `ATV_CONFIG` environment variable
3. `$XDG_CONFIG_HOME/atv/config.json`, or `~/.config/atv/config.json` when that is unset
4. no file: the defaults in the table above

```json
{
  "port": 8765,
  "scaffolds": {
    "claude": { "paths": ["~/.claude/projects", "~/synced/laptop/claude-projects"] },
    "opencode": { "paths": ["~/.local/share/opencode/opencode.db"] }
  }
}
```

- Every key is optional; anything left out keeps its default.
- `paths` is a list, so a scaffold can have several locations. An empty list turns that scaffold off.
- `~` and environment variables (`$HOME`, `${NAME}`) are expanded.
- A listed path that does not exist is skipped with a warning.
- Malformed JSON, an unknown scaffold name, or a file named by `--config` / `ATV_CONFIG` that is missing
  stops the server at startup with an error naming the file.
- The port is taken from the command line (`python3 server.py 9000` or `--port 9000`), then the file, then 8765.
  `--port 0` lets the OS pick a free one; the server prints `listening on http://127.0.0.1:<port>` either way.
  The desktop app always uses a free port and ignores `port`.

## Under the hood

- `server.py` turns both formats into one event shape and serves it as JSON.
- `index.html` is the whole frontend, no dependencies.
- `electron/main.js` is the desktop shell: it starts `server.py` and points a window at it.
- `test_server.py` is a small self-check: `python3 test_server.py`.
