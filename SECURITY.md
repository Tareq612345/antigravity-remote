# Security Policy

## Unofficial Project Disclaimer
**Antigravity Remote** is an unofficial, community-built remote control bridge for Google Antigravity (`agy` CLI and local IDE transcripts). It is not affiliated with, endorsed by, or supported by Google.

## How Authentication & Tokens Work
1. **Local Access Token (`config.json`):**
   - Generated automatically on first launch in `%USERPROFILE%\.antigravity-remote\config.json` (never committed to git).
   - Used only for local loopback (`127.0.0.1`) control panel requests via the `Authorization: Bearer <token>` header.
2. **Remote Access (Supabase + Google OAuth):**
   - When using the hosted web client, users authenticate with Google OAuth via Supabase.
   - The web client sends its short-lived Supabase JWT in the `Authorization: Bearer <sb_token>` header (or as the first WebSocket authentication frame).
   - The desktop server verifies the JWT with Supabase and enforces that the authenticated email strictly matches `owner_email` stored on the PC.
3. **One-Time PC Pairing Code:**
   - `/pc-login` is restricted to `127.0.0.1` / `localhost` and uses a short-lived, single-use pairing code (`5 minutes` TTL) rather than embedding the master token in HTML.
4. **Network Binding:**
   - By default, the server binds to `127.0.0.1` only. Remote access happens over an encrypted Cloudflare Tunnel (`cloudflared`) rather than exposing a raw LAN port.

## Reporting a Vulnerability
If you discover a security vulnerability in this repository:
1. **Do not** open a public GitHub issue with exploit details.
2. Report it privately via GitHub Security Advisories or by contacting the repository maintainer directly.
3. Include steps to reproduce, affected files, and potential impact. We will acknowledge and address valid reports promptly.
