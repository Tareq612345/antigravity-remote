<div align="center">
  <img src="docs/app.png" alt="Antigravity Remote" width="110" />
  <h1>Antigravity Remote</h1>
  <p><strong>Secure mobile &amp; web remote control for Google Antigravity on your PC</strong></p>

  <p>
    <img src="https://img.shields.io/badge/Platform-Windows%2010%2F11-0078D6?style=flat-square" alt="Windows" />
    <img src="https://img.shields.io/badge/Python-3.11%2B-3776AB?style=flat-square" alt="Python" />
    <img src="https://img.shields.io/badge/Tunnel-Cloudflare-F38020?style=flat-square" alt="Cloudflare" />
    <img src="https://img.shields.io/badge/Auth-Supabase%20%2B%20Google-3ECF8E?style=flat-square" alt="Supabase" />
  </p>

  <p>
    <a href="docs/GUIDE.en.md"><b>English Guide</b></a>
    &nbsp;|&nbsp;
    <a href="docs/GUIDE.ar.md"><b>دليل عربي</b></a>
  </p>
</div>

---

## Overview

**Antigravity Remote** lets you chat with your local **Google Antigravity** (`agy` CLI) session, browse and transfer workspace files, switch models and thinking effort, and monitor local IDE transcripts from your phone or any browser — without screen sharing or opening router ports.

> [!NOTE]
> This is an **unofficial**, community-built project and is not affiliated with or endorsed by Google.

---

## Key Features

- **Direct `agy` CLI Integration:** Uses your existing Antigravity account, subscription models, and tools in the background.
- **Model, Thinking Effort & Usage Viewer:** Switch between available models (`Gemini`, `Claude`, `GPT-OSS`), adjust thinking effort (`low`, `medium`, `high`, `xhigh`), and inspect session token usage.
- **Live IDE Transcript Mirror:** View and follow your local Antigravity IDE conversations in real time.
- **Message Queue & Cancel:** Queue follow-up prompts while the agent is still working, or stop a running turn anytime.
- **Workspace File Explorer & Safe Uploads:** Browse project files, download files to mobile, and upload images, audio, or documents into `_uploads/`.
- **Sliding Windows Sleep Manager:** Keeps your PC awake (while allowing the display to turn off) for 7 hours and automatically resets the timer on every interaction.
- **Lightweight Desktop App + System Tray:** Built with `pywebview` (Edge WebView2) and `pystray` for minimal RAM and CPU usage.
- **Zero-Config Remote Access:** Automatically spawns a SHA256-verified **Cloudflare Tunnel** and pairs your PC to your Google account via **Supabase** with Row Level Security (RLS).

---

## Architecture

```text
[ Mobile / Web UI (GitHub Pages) ]
        │  Google OAuth (Supabase) + Authorization Bearer JWT
        ▼
[ Cloudflare Quick Tunnel (HTTPS / WSS) ]
        │
        ▼
[ Local Desktop Server (127.0.0.1:8765) ] ──▶ [ agy.exe / IDE Transcripts ]
```

---

## Quick Start

### Option 1: Standalone Windows Executable
1. Make sure **`agy` CLI** is installed and signed in on your PC (`~/.gemini/bin/agy.exe`).
2. Run **`AntigravityRemote.exe`** (it automatically downloads and SHA256-verifies `cloudflared.exe` if needed).
3. Click **Sign in with Google** to link your PC, then click **Confirm & Link This PC**.
4. Open the web client on your phone (`https://antigravity-remote-8fi.pages.dev/`) and sign in with the **same Google account**.

### Option 2: Run from Source
```bash
git clone https://github.com/Tareq612345/antigravity-remote.git
cd antigravity-remote

python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

python desktop_app.py
```

Or run the headless server directly:
```bash
uvicorn server:app --host 127.0.0.1 --port 8765 --no-access-log
```

---

## Self-Hosting Setup (Supabase + GitHub Pages)

1. Create a Supabase project and run [`supabase_schema.sql`](supabase_schema.sql) in the SQL Editor to create `public.remote_devices` with Row Level Security enabled.
2. Enable **Google OAuth** under `Authentication -> Providers -> Google` and disable public email signups if desired.
3. Update `SUPABASE_URL` and `SUPABASE_ANON_KEY` in [`tunnel_and_auth.py`](tunnel_and_auth.py) and [`docs/index.html`](docs/index.html).
4. Enable **GitHub Pages** on your fork serving from `/docs`.

---

## Building the Executable

```bash
pip install pyinstaller
pyinstaller --noconfirm --onefile --noconsole --name AntigravityRemote --icon "static\app.ico" --add-data "static;static" --exclude-module google.antigravity --exclude-module google.genai --exclude-module mcp --exclude-module tkinter --exclude-module unittest --exclude-module pydoc desktop_app.py
```

### Latest Build Checksum
- `AntigravityRemote.exe` (`SHA256`):
  ```text
  22ADE978E5DCB19943FEA5487D99218D77B69E4C673F8F354A677641EBE3631F
  ```

---

## Testing & Security

Run the unit test suite and dependency audit locally:
```bash
python -m unittest discover -s tests -v
pip-audit -r requirements.txt
```

See [`SECURITY.md`](SECURITY.md) for authentication details, file permission hardening, and vulnerability reporting.
