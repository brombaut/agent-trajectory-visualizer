# ATV configuration

Optional, and shared by both ways of running. To look somewhere else, or in several places, write
a JSON file. The first of these wins:

1. `--config <path>` (`python3 server.py --config <path>` or `npm start -- --config <path>`)
2. the `ATV_CONFIG` environment variable
3. `$XDG_CONFIG_HOME/atv/config.json`, or `~/.config/atv/config.json` when that is unset
4. no file: the defaults, which are the paths shown in the example below

```json
{
  "port": 8765,
  "scaffolds": {
    "claude": { "paths": ["~/.claude/projects"] },
    "opencode": { "paths": ["~/.local/share/opencode/opencode.db"] },
    "codex": { "paths": ["~/.codex/sessions"] }
  },
  "projects": {
    "~/work/some-project": { "color": "#00e5ff" }
  }
}
```

- Every key is optional; anything left out keeps its default.
- `paths` is a list, so a scaffold can have several locations, for example a copy synced from
  another machine. An empty list turns that scaffold off.
- `~` and environment variables (`$HOME`, `${NAME}`) are expanded.
- A listed path that does not exist is skipped with a warning.
- `projects` gives a project a fixed colour in place of the one hashed from its path (there is none by default;
  the one above is only an example). The key is the project's directory, matched exactly against a session's
  working directory, so a subdirectory is a separate project; subagents take their parent's colour. `color` is
  any CSS colour. Titles are drawn on it in black, so pick a light one.
- Malformed JSON, an unknown scaffold name, a `color` that is not a string, or a file named by `--config` / `ATV_CONFIG` that is missing
  stops the server at startup with an error naming the file.
- The port is taken from the command line (`python3 server.py 9000` or `--port 9000`), then the file, then 8765.
  `--port 0` lets the OS pick a free one; the server prints `listening on http://127.0.0.1:<port>` either way.
- The desktop app always uses a free port and ignores `port`.
