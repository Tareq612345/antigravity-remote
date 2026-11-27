# Antigravity Remote — Install & Run Guide

[العربية](GUIDE.ar.md) · **English**

A complete, step-by-step guide to running **Antigravity Remote** on your PC and controlling your local Antigravity (`agy`) session from your phone.

> **Unofficial project.** Antigravity Remote is community-built. It is not affiliated with, endorsed by, or supported by Google. Everything here is based on the actual code in this repository.

---

## Contents

1. [What you need](#1-what-you-need)
2. [Install on the PC](#2-install-on-the-pc)
3. [Start the server](#3-start-the-server)
4. [Pair the PC with your Google account](#4-pair-the-pc-with-your-google-account)
5. [Sign in from your phone](#5-sign-in-from-your-phone)
6. [The Cloudflare tunnel](#6-the-cloudflare-tunnel)
7. [Modes: plan vs accept-edits](#7-modes-plan-vs-accept-edits)
8. [Workspace and file access](#8-workspace-and-file-access)
9. [Uploads from your phone](#9-uploads-from-your-phone)
10. [Keeping the PC awake](#10-keeping-the-pc-awake)
11. [Signing out](#11-signing-out)
12. [Security notes](#12-security-notes)
13. [Troubleshooting](#13-troubleshooting)
14. [Self-hosting your own copy](#14-self-hosting-your-own-copy)

---

## 1. What you need

| Requirement | Details |
| --- | --- |
| **Windows 10 or 11** | The desktop app, the sleep manager, and the autostart script are Windows-only. The server itself is Python and can run elsewhere, but the supported path is Windows. |
| **Python 3.11 or newer** | Only if you run from source. Not needed for the prebuilt `.exe`. |
| **Antigravity (`agy`) installed and signed in** | The server launches `%USERPROFILE%\.gemini\bin\agy.exe`. The agent runs under *that* local login — not under your web login. |
| **Internet access** | Needed for the Cloudflare tunnel and for Supabase sign-in. |
| **A Google account** | The same account is used on the PC (pairing) and on the phone (sign-in). |

> **Two separate accounts.** Your remote sign-in (Supabase + Google) only authorises the phone to talk to *your* PC. The chat itself runs under the Antigravity login already stored on the PC by `agy`. No Antigravity credential is ever stored by this app or sent over the network.

---

## 2. Install on the PC

### Option A — prebuilt executable

1. Install and sign in to the **`agy` CLI** on the PC.
2. Run **`AntigravityRemote.exe`**. On first launch it downloads `cloudflared.exe` (pinned version, SHA256-verified) into `%USERPROFILE%\.antigravity-remote\`.

### Option B — run from source

```bat
git clone https://github.com/Tareq612345/antigravity-remote.git
cd antigravity-remote

python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

`requirements.txt` pins every dependency (FastAPI, uvicorn, pydantic, requests, pywebview, pystray, Pillow, python-multipart, websockets).

On first start the server creates:

```text
%USERPROFILE%\.antigravity-remote\config.json   ← access token, workspace, mode, port
%USERPROFILE%\.antigravity-remote\chats.json    ← local chat history
%USERPROFILE%\.antigravity-remote\audit.log     ← auth / upload / turn events
%USERPROFILE%\.antigravity-remote\uploads\      ← legacy upload folder (older versions)
```

These files are created with permissions restricted to your Windows user (`icacls`). They are never committed to git.

---

## 3. Start the server

### Desktop app (recommended)

```bat
python desktop_app.py
```

This starts the server on `127.0.0.1:8765`, opens the control panel window, and adds a tray icon with:

- **Show Control Panel**
- **Open Mobile Web**
- **Quit**

Closing the window minimises it to the tray; the server keeps running.

### Headless server

```bat
uvicorn server:app --host 127.0.0.1 --port 8765 --no-access-log
```

### `start_server.bat`

The included batch file starts the headless server from a virtual environment:

```bat
@echo off
cd /d "C:\Projects\antigravity-remote"
".venv\Scripts\python.exe" -m uvicorn server:app --host 127.0.0.1 --port 8765 --no-access-log
```

> **Edit the path first.** It assumes the project lives in `C:\Projects\antigravity-remote`. Change both lines to your own folder.

### Start automatically at logon (optional)

Run `setup_autostart.ps1` **once, as Administrator**. It registers a scheduled task named `AntigravityRemoteServer` that runs `start_server.bat` at logon.

> The script also hardcodes `C:\Projects\antigravity-remote` — edit it if your path is different.

The server binds to `127.0.0.1` only. Nothing on your Wi-Fi can reach it directly; remote access happens exclusively through the Cloudflare tunnel.

---

## 4. Pair the PC with your Google account

Pairing tells the server *which* Google account is allowed to control this PC.

1. Open the control panel (tray icon → **Show Control Panel**).
2. Click **Sign in with Google**. This opens `http://127.0.0.1:8765/pc-login` in your browser.
3. Sign in with your Google account.
4. Click **Confirm & Link This PC**.

What happens behind the scenes:

- `/pc-login` only answers requests that arrive from `127.0.0.1` — it is unreachable through the tunnel.
- The page issues a **one-time pairing code** (5-minute lifetime) and an `httpOnly` pairing cookie. Both are required to complete the link.
- On confirm, the account email is saved as `owner_email` in `config.json`, and the current tunnel URL is written to your Supabase `remote_devices` row so the phone can find the PC.
- Every later request must come from that same email; any other account is rejected.

The control panel shows **Linked to: your@email** once pairing succeeds.

---

## 5. Sign in from your phone

1. Open the web client: **https://tareq612345.github.io/antigravity-remote/**
2. Tap **Sign in with Google** and use the **same account** you paired with.
3. The app looks up your device row, gets the tunnel URL, and connects.

You should see the green connection state next to **Antigravity** in the sidebar, your recent chats, and the current model.

From there you can:

- Send prompts and queue follow-ups while the agent is working
- Stop a running turn
- Browse the workspace, download files, upload files
- Switch model and thinking effort
- See session usage
- Extend the sleep timer

---

## 6. The Cloudflare tunnel

The server starts a **Cloudflare Quick Tunnel** automatically on boot, through `cloudflared.exe`.

- If `cloudflared.exe` is missing, the server downloads version **2026.11.2** from Cloudflare's official GitHub release and verifies its **SHA256** before running it. A mismatch aborts the download.
- The tunnel gives you a public HTTPS URL of the form `https://<random-words>.trycloudflare.com`.
- **That URL changes every time the server starts.** The app re-publishes the new URL to Supabase automatically, so you normally do not need to do anything.
- The current URL is always visible in the control panel under the tunnel section.

You do not need a Cloudflare account, a domain, or any router port forwarding.

---

## 7. Modes: plan vs accept-edits

The agent runs with one of two modes, stored as `agy_mode` in `config.json`:

| Mode | What it means |
| --- | --- |
| **`plan`** | The agent reads, reasons, and proposes changes but does **not** apply file edits on its own. Safer: you review before anything changes. |
| **`accept-edits`** | The agent applies file edits directly as it works. Faster, but a wrong or unexpected instruction changes real files without a separate approval step. |

- The default for a fresh install is **`accept-edits`** (`DEFAULT_AGY_MODE` in `server.py`). Change it if you want the safer behaviour.
- You can switch modes from the control panel on the PC, and from the **Settings** view in the phone app.
- Anything typed into the chat is executed by a real agent with access to your workspace. Treat the access token like a password: whoever holds it can drive the agent.

---

## 8. Workspace and file access

The agent runs with its working directory set to your **workspace** (default: your Desktop).

- Change it from the control panel: type the folder path, then **Save**.
- The path must exist and must be a directory.
- Every folder you pick on the PC is added to an **allowlist** (`allowed_workspaces`).
- From the phone you can only switch to a folder that is **already on the allowlist**. To use a new folder, add it once from the PC.
- All file browsing, preview, download, and upload paths are validated: anything resolving outside the workspace is rejected.

---

## 9. Uploads from your phone

- Files you upload from the phone are saved into `<workspace>\_uploads\`.
- Maximum size: **10 MB** per file.
- Allowed extensions: `.txt .md .json .csv .log .png .jpg .jpeg .webp .gif .mp3 .wav .m4a .ogg .pdf`.
- Executable and script types (`.exe`, `.bat`, `.ps1`, `.py`, `.js`, and similar) are rejected.
- File names are sanitised before saving.
- Because uploads live inside the workspace, the agent can open them in the same turn.

---

## 10. Keeping the PC awake

The server keeps Windows awake with a **sliding 7-hour window** that resets on every interaction (a prompt, a status check, a websocket connection).

- **Extend 7 Hours** in the control panel (or the sleep control in the phone app) pushes the deadline further out.
- **Toggle Keep Screen On** decides whether the display may turn off while the machine stays awake. Off is the default and saves power.

---

## 11. Signing out

- **Phone / web:** Settings → **Switch Account / Sign Out**. This calls the server's logout endpoint (which drops its cached session immediately) and then signs out of Supabase on the device.
- **PC:** closing the control panel only minimises it. Use tray → **Quit** to stop the server and the tunnel.
- To unlink the PC from an account entirely, quit the app and remove `owner_email` from `%USERPROFILE%\.antigravity-remote\config.json`, then pair again.

---

## 12. Security notes

- **The local token in `config.json` is the master key for the PC.** Anyone who has it can drive the agent. Never paste it anywhere public.
- **The server listens on `127.0.0.1` only.** Remote access is exclusively through the Cloudflare tunnel.
- **Remote sign-in is Supabase + Google OAuth.** The server verifies the token with Supabase and requires the email to match `owner_email`.
- **Rate limits:** per-IP limits on API calls and on failed authentication; at most **10** concurrent websocket clients.
- **Audit log:** `audit.log` records authentication failures, uploads, workspace changes, and agent turns.
- **`config.json`, `chats.json`, `audit.log`** are restricted to your Windows user account.
- **`cloudflared.exe`** is version-pinned and SHA256-verified before execution.
- **Uploads** are limited in size and type, and are stored inside the workspace so the agent can read them.
- **What leaves your PC:** prompts, agent responses, and the IDE transcript mirror — that is the point of the app. No Antigravity credentials, tokens, or passwords are transmitted.
- If you self-host Supabase, enable **Row Level Security** on `remote_devices` (see `supabase_schema.sql`) and consider disabling public sign-ups.

---

## 13. Troubleshooting

**"agy.exe not found"**
`agy` is not installed at `%USERPROFILE%\.gemini\bin\agy.exe`. Install and sign in to the Antigravity CLI, or set `agy_path` in `config.json` to the correct executable.

**Tunnel never comes online / no `trycloudflare.com` URL**
Check your internet connection and whether `cloudflared` is blocked by antivirus or a corporate proxy. If a corrupted binary is suspected, delete `%USERPROFILE%\.antigravity-remote\cloudflared.exe` and restart — it will be downloaded and verified again. A SHA256 mismatch always deletes the file rather than running it.

**The phone shows "Connecting…" forever**
The tunnel URL the phone knows is stale. Make sure the PC is running and paired; the app republishes the new URL on each start. Reload the web app.

**"Unauthorized" (401) on the phone**
Your Supabase session expired, or the signed-in email no longer matches `owner_email`. Sign out and sign in again with the paired account.

**"Workspace not in allowlist" (403)**
You tried to switch to a folder from the phone that was never added on the PC. Add it once in the control panel, then retry.

**Pairing page says "Invalid or expired pairing code"**
The code lives 5 minutes and works once. Open `http://127.0.0.1:8765/pc-login` again from the PC to get a fresh one.

**`/pc-login` returns 403 "only allowed from localhost"**
You opened it through the tunnel. It only works from the PC itself.

**Port 8765 already in use**
Another instance is still running. The desktop app kills stale instances on start; for a manual run, close the old process or change `port` in `config.json`.

**Local page asks for an Access Token**
That is the local web UI at `http://127.0.0.1:8765`. Paste the `token` value from `config.json`.

**The window is blank / WebView fails**
The desktop app uses `pywebview` with the Edge WebView2 runtime. Install the WebView2 runtime, or run the headless server and use your browser.

---

## 14. Self-hosting your own copy

1. Create a Supabase project.
2. Run [`supabase_schema.sql`](../supabase_schema.sql) in the SQL Editor — it creates `public.remote_devices` with RLS enabled and per-user policies.
3. Enable **Google** under Authentication → Providers.
4. Put your project URL and anon key into [`tunnel_and_auth.py`](../tunnel_and_auth.py) and [`docs/index.html`](index.html).
5. Update `DEFAULT_ALLOWED_ORIGINS` in `server.py` if your Pages site uses a different origin.
6. Fork the repo, enable **GitHub Pages** serving from `/docs`, and open your own Pages URL on the phone.

The Supabase anon key is designed to be public; your data is protected by the RLS policies, not by hiding the key.

---

*Unofficial project. Use at your own risk, and keep the access token private.*
