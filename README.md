# antigravity-remote

Control the Antigravity sessions on my own PC from a browser on any other device.

A small FastAPI server that runs on the Windows machine where Antigravity is installed, and serves a web page you can open from a phone or another computer.

## What is in the repo

- `server.py` - the FastAPI app. Serves the web UI in `static/`, lists the Antigravity conversations and handles chat actions.
- `ide_transcript_watcher.py` - reads the Antigravity CLI conversation files under `~/.gemini/antigravity-cli/conversations` and the IDE brain folder, and turns them into messages the page can show and continue.
- `tunnel_and_auth.py` - opens a Cloudflare quick tunnel with `cloudflared` so the server is reachable from outside, and puts Supabase email/password sign-in in front of it.
- `sleep_manager.py` - keeps Windows awake while the page is being used, so the machine does not fall asleep in the middle of a session.
- `start_server.bat` and `setup_autostart.ps1` - start the server on Windows, optionally when I log in.

## Run

```
pip install fastapi uvicorn requests
python server.py
```

Then open the address the server prints and sign in.

## Status

Personal project, early, Windows only. No releases yet.
