# ATV — Agent Trajectory Visualizer

![A rider on an ATV climbing a trail made of user, tool and assistant turns](assets/atv-header.png)

A quick-and-dirty browser view for going off-road through your coding-agent sessions: every
project, every session, every turn, live as it happens. It reads [Claude Code](https://claude.com/claude-code)
and [opencode](https://opencode.ai) sessions straight from where those tools already store them.

## Ride

```sh
python3 server.py        # then open http://127.0.0.1:8765
python3 server.py 9000   # or pick a port
```

Python 3 standard library only. Nothing to install, nothing to build.

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

## Under the hood

- `server.py` turns both formats into one event shape and serves it as JSON.
- `index.html` is the whole frontend, no dependencies.
- `test_server.py` is a small self-check: `python3 test_server.py`.
