Agent Mirror Dashboard

What it does
- Mirrors local Hermes, OpenCode, and Cursor activity into one web UI.
- Shows running agent-related processes with running/idle activity state.
- Shows tmux sessions with active/idle state.
- Reads Hermes session/message data from ~/.hermes/state.db.
- Reads OpenCode session/message/part data from ~/.local/share/opencode/opencode.db.
- Reads Cursor agent transcripts and terminal snapshots from ~/.cursor/projects.
- Polls every 2.5 seconds so you can watch progress from another device/browser while Telegram stays minimal.

Run it
1. cd /Users/mraihanparlaungan/agent-mirror-dashboard
2. python3 app.py
3. Open http://127.0.0.1:8787

Expose to your LAN
- It already binds to 0.0.0.0 by default.
- Find your Mac IP with: ipconfig getifaddr en0
- Then open: http://YOUR_MAC_IP:8787 from your phone or another machine on the same network.

Useful endpoints
- /                UI
- /healthz         health check
- /api/state       everything as JSON
- /api/hermes      Hermes only
- /api/opencode    OpenCode only
- /api/cursor      Cursor only

Notes
- This is read-only.
- It reads whatever those tools persist locally, including reasoning/tool traces when available.
- Cursor visibility depends on what Cursor wrote into transcript JSONL and terminal snapshot files.
