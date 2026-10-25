# ATV — Agent Trajectory Visualizer

![A rider on an ATV climbing a trail made of user, tool and assistant turns](assets/atv-header.png)

A quick-and-dirty view for going off-road through your coding-agent sessions: every
project, every session, every turn, live as it happens. Open it in a browser tab or run it as its
own desktop window. It reads [Claude Code](https://claude.com/claude-code),
[opencode](https://opencode.ai) and [Codex](https://github.com/openai/codex) sessions straight from where those tools already store them.

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

Closing the window quits the app. For the sandbox error Electron gives on recent Ubuntu, and for
adding ATV to the application menu, see [electron/README.md](electron/README.md).

## The terrain

![Three sessions side by side, each with its time strip, the middle one with three subagent lanes; a failed tool call and an assistant reply expanded](assets/screenshot.png)

- **Sessions** on the left, newest first, with subagent sessions nested under their parent. Each is tinted
  with its project's colour (a project is a working directory), and the dropdown above filters to one or more projects.
  A 🔔 marks a session that is waiting for you.
- **Trajectory** on the right: one line per user message, assistant reply and tool call.
  Click a line for the full text, tool inputs, output and token details, or hit **Expand all**.
  The line under a column's header says where the session's time went (the model, tools, you, background
  work, idle) and how full its context is; hover it for what each part means. In a wide column a strip under
  it draws the same as a timeline, with a lane per subagent and the context size as a line; click a stretch to
  jump to it, or a lane to open that subagent.
- **Side by side**: Ctrl+click (Cmd+click on macOS) a session to open it in another column, or again to
  close it. Drag the divider between two columns to resize
  them. The URL keeps the set of open sessions, so a reload restores it.
- **Focus**: ⤢ in a column's header shows that session alone, full width. ⤢ again or Esc puts the columns back.
- **Live**: every open session is tailed every 1.5s and follows the bottom unless you scroll that column up.
  **Open live** opens every session still running (within the project filter and search) and closes the rest.
- **meta** shows the background records hidden by default: system reminders, hook output, attachments.
- **details** adds each expanded line's raw metadata (model, token usage, stop reason) as JSON.

## Where it looks

| Tool        | Source                                  |
| ----------- | --------------------------------------- |
| Claude Code | `~/.claude/projects/**/*.jsonl`          |
| opencode    | `~/.local/share/opencode/opencode.db`    |
| Codex       | `~/.codex/sessions/**/rollout-*.jsonl`   |

Everything is read-only, and the server only listens on localhost, since sessions tend to contain secrets.

## Configuration

Optional. To look somewhere else, or in several places, write a JSON config file:
see [CONFIG.md](CONFIG.md).
