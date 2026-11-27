# Antigravity Remote — Quick Start Guide (English)

Control your PC's **Google Antigravity** session directly from your phone in **3 simple steps**.

---

## Before You Start
- Windows 10 or Windows 11 PC.
- **`agy` CLI** installed and signed in on your PC (verify by running `agy` in a terminal once). The app starts `agy` itself for every message, so the CLI has to work on its own. The Antigravity IDE does not need to be open, and no chat session has to be running in it.

---

## Step 1: Download & Launch on Your PC
1. Download **`AntigravityRemote.exe`** — release [`0.1`](https://github.com/Tareq612345/antigravity-remote/releases/tag/0.1) (pre-release), 34,532,706 bytes (32.9 MB).
   - Direct link: <https://github.com/Tareq612345/antigravity-remote/releases/download/0.1/AntigravityRemote.exe>
   - Optional check before running it: in PowerShell, `Get-FileHash .\AntigravityRemote.exe -Algorithm SHA256` must print
     `96862FD0F74B90002D84CE259E1D4FECFF2945075933981F6A07942F57E2909B`.
2. Double-click **`AntigravityRemote.exe`**.
   - On first launch, it automatically downloads and verifies `cloudflared` (takes a few seconds) and opens an encrypted tunnel.
   - Wait until the **Connection & Cloudflare Tunnel** card says **`Online`**.

## Step 2: Link Your Google Account (One-Time)
1. In the desktop window, click **Sign in with Google**.
2. Sign in with your Google account in the browser tab that opens.
3. Click **Confirm & Link This PC**.
4. You can now close the window (`X`) — it stays running quietly in the Windows System Tray next to the clock.

## Step 3: Open from Your Phone
1. Open **[https://antigravity-remote-8fi.pages.dev/](https://antigravity-remote-8fi.pages.dev/)** on your mobile browser (works over Wi-Fi or 4G/5G).
2. Click **Sign in with Google** using the **exact same Google account**.
3. Your phone will automatically find and connect to your PC!

---

## Quick Tips
- **Switch Model / Thinking Effort:** Tap the `⚡ Default Model` button below the chat box to pick a model, adjust thinking effort, or view session token usage.
- **Send Images / Files:** Tap `＋` in the chat box to upload files safely to `_uploads/` inside your workspace.
- **Change Language:** Go to **Settings -> Language** to switch between **English** and **العربية**.
- **Keep PC Awake:** The app keeps your PC awake for 7 hours (while allowing the monitor to turn off) and resets the timer on every message.
