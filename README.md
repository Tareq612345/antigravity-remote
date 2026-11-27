# Antigravity Remote

An unofficial remote control bridge and mobile-friendly web UI for Google Antigravity (`agy` CLI & local IDE transcripts), featuring Cloudflare Tunnel, Supabase Google OAuth verification, Windows sleep management, and a lightweight desktop control panel.

## Requirements
- Windows 10/11 (for desktop app & sleep manager)
- `agy` CLI installed and signed in (`~/.gemini/bin/agy.exe`)
- Python 3.11+ (if running from source)

## Running from Source
```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python desktop_app.py
```

Or run the headless server directly:
```bash
uvicorn server:app --host 127.0.0.1 --port 8765 --no-access-log
```

## Building the Standalone Windows Executable
```bash
pip install pyinstaller
pyinstaller --noconfirm --onefile --noconsole --name AntigravityRemote --icon "static\app.ico" --add-data "static;static" --exclude-module google.antigravity --exclude-module google.genai --exclude-module mcp --exclude-module tkinter --exclude-module unittest --exclude-module pydoc desktop_app.py
```

### Latest Release Checksum
- `AntigravityRemote.exe` SHA256: `6A60FC5BE8F5BC2BEEB379DA7C0F6B9E79C39A389218637D830432009B8F1BAE`

## Security
See [SECURITY.md](SECURITY.md) for authentication details, file permission hardening, and vulnerability reporting.
