# Agent Session Mirror

**Local Agent Observatory** — a dark, glassy dashboard that mirrors Hermes, OpenCode, and Cursor activity into one live web UI.

![Agent Session Mirror dashboard](docs/dashboard.png)

Watch agent progress from another device or browser while keeping Telegram minimal. The UI refreshes every 2.5 seconds and stays read-only.

## What you get

- **Three-pane layout**: session rail, chat stage, and runtime drawer
- **Unified sessions** from Hermes, OpenCode, and Cursor with source-colored badges
- **Chat-style timeline** with flat user bubbles, markdown rendering, and tool/reasoning/terminal events
- **Runtime panel** for running/idle processes, tmux sessions, and secondary activity
- **Source filters** for All, Hermes, OpenCode, and Cursor

## Data sources

| Source | Location |
|--------|----------|
| Hermes | `~/.hermes/state.db` |
| OpenCode | `~/.local/share/opencode/opencode.db` |
| Cursor | `~/.cursor/projects` transcripts and terminal snapshots |

## Quick start

```bash
git clone git@github.com:RaihanParl/agent-monitor.git
cd agent-monitor
python3 app.py
```

Open [http://127.0.0.1:8787](http://127.0.0.1:8787)

## Expose on your LAN

The server binds to `0.0.0.0` by default.

```bash
ipconfig getifaddr en0
```

Then open `http://YOUR_MAC_IP:8787` from your phone or another machine on the same network.

## API endpoints

| Endpoint | Description |
|----------|-------------|
| `/` | Web UI |
| `/healthz` | Health check |
| `/api/state` | Full state as JSON |
| `/api/hermes` | Hermes data only |
| `/api/opencode` | OpenCode data only |
| `/api/cursor` | Cursor data only |

## Notes

- Read-only: this dashboard does not send commands to agents.
- It reads whatever those tools persist locally, including reasoning and tool traces when available.
- Cursor visibility depends on transcript JSONL and terminal snapshot files on disk.

## Regenerate the README screenshot

```bash
python3 app.py
npm install
node capture_readme_screenshot.mjs
```
