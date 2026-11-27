# Security Policy

## Unofficial Project Disclaimer
**Antigravity Remote** is an unofficial, community-built remote control bridge for Google Antigravity (`agy` CLI and local IDE transcripts). It is not affiliated with, endorsed by, or supported by Google.

## How Authentication, Tokens & Local Data Work
1. **Local Access Token & Restricted File Permissions:**
   - `config.json`, `chats.json`, and `audit.log` are stored in `%USERPROFILE%\.antigravity-remote\` (never committed to git).
   - File permissions on `config.json`, `chats.json`, and `audit.log` are restricted to the current Windows user only via `icacls` (`0600` on POSIX) so other local users cannot read chat history or tokens.
   - Local desktop UI (`desktop_app.py`) retrieves the token via the native `pywebview` JS bridge (`Authorization: Bearer <token>`), never in the URL.
2. **Remote Access (Supabase + Google OAuth):**
   - When using the hosted web client, users authenticate with Google OAuth via Supabase and send their Supabase JWT in the `Authorization: Bearer` header (or as the first WebSocket authentication frame).
   - The desktop server verifies the JWT with Supabase (short 45-second cache, invalidated immediately on `/api/logout`) and enforces that the authenticated email strictly matches `owner_email`.
3. **One-Time PC Pairing Code & Confirmation:**
   - `/pc-login` is restricted to `127.0.0.1` / `localhost`, sets an `httpOnly` `SameSite=Strict` pairing cookie, and requires an explicit user click on **Confirm & Link This PC** before linking.
4. **Rate Limiting, WebSocket Cap & Tunnel Integrity:**
   - Behind Cloudflare Tunnel (`127.0.0.1`), HTTP and WebSocket rate limiters key on `cf-connecting-ip` with separate buckets for normal API traffic and failed authentication attempts.
   - Concurrent WebSocket connections are capped (`MAX_WS_CLIENTS = 10`).
   - `cloudflared.exe` downloads are pinned to a specific release version and verified against a hardcoded `SHA256` hash before execution.
5. **Uploads Outside Workspace:**
   - Uploaded files are stored in `%USERPROFILE%\.antigravity-remote\uploads` (outside the project workspace) and restricted to non-executable data/media extensions (`<= 10 MB`).

## Reporting a Vulnerability
If you discover a security vulnerability in this repository:
1. **Do not** open a public GitHub issue with exploit details.
2. Report it privately via GitHub Security Advisories or by contacting the repository maintainer directly.
3. Include steps to reproduce, affected files, and potential impact. We will acknowledge and address valid reports promptly.
