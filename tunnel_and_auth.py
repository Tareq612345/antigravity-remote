import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import requests

SUPABASE_URL = "https://jpivlsdeiompylybbgyg.supabase.co"
SUPABASE_ANON_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImpwaXZsc2RlaW9tcHlseWJiZ3lnIiwicm9sZSI6ImFub24iLCJpYXQiOjE3OTU3Mzc4NjEsImV4cCI6MjExMTMxMzg2MX0."
    "tw6-333dknhvOtoMXf81DE-gbY-Y09agbptZZSEXZK8"
)
TRYCLOUDFLARE_RE = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")


def find_cloudflared(data_dir: Path | None = None) -> str | None:
    found = shutil.which("cloudflared")
    if found:
        return found
    candidates = [
        Path(r"C:\Program Files (x86)\cloudflared\cloudflared.exe"),
        Path(r"C:\Program Files\cloudflared\cloudflared.exe"),
        Path.home() / "AppData" / "Local" / "Microsoft" / "WinGet" / "Links" / "cloudflared.exe",
    ]
    if data_dir:
        candidates.insert(0, data_dir / "cloudflared.exe")
    for c in candidates:
        if c.exists():
            return str(c)
    return None


def ensure_cloudflared(data_dir: Path) -> str | None:
    exe = find_cloudflared(data_dir)
    if exe:
        return exe
    dest = data_dir / "cloudflared.exe"
    url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
    try:
        print("[Tunnel] Downloading cloudflared...")
        with requests.get(url, stream=True, timeout=60) as r:
            r.raise_for_status()
            with dest.open("wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 128):
                    if chunk:
                        f.write(chunk)
        return str(dest)
    except Exception as exc:
        print(f"[Tunnel] Failed to download cloudflared: {exc}")
        return None


class TunnelManager:
    """Runs cloudflared quick tunnel and syncs the public HTTPS URL to Supabase."""

    def __init__(self, local_port: int = 8765, data_dir: Path | None = None) -> None:
        self.local_port = local_port
        self.data_dir = data_dir or (Path.home() / ".antigravity-remote")
        self.proc: subprocess.Popen[str] | None = None
        self.public_url: str | None = None
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self, on_url_ready=None) -> None:
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop, args=(on_url_ready,), daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()

    def _run_loop(self, on_url_ready) -> None:
        exe = ensure_cloudflared(self.data_dir)
        if not exe:
            print("[Tunnel] cloudflared unavailable; running LAN-only.")
            return
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.proc = subprocess.Popen(
            [exe, "tunnel", "--url", f"http://127.0.0.1:{self.local_port}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creationflags,
        )
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            if self._stop_event.is_set():
                break
            m = TRYCLOUDFLARE_RE.search(line)
            if m and not self.public_url:
                self.public_url = m.group(0)
                print(f"[Tunnel] Public URL ready: {self.public_url}")
                if on_url_ready:
                    try:
                        on_url_ready(self.public_url)
                    except Exception as exc:
                        print(f"[Tunnel] Callback error: {exc}")


class SupabaseAuthClient:
    """Handles token verification, refresh, and device registration in Supabase."""

    def __init__(self, url: str = SUPABASE_URL, anon_key: str = SUPABASE_ANON_KEY) -> None:
        self.url = url.rstrip("/")
        self.anon_key = anon_key
        self._cache: dict[str, tuple[float, dict[str, Any]]] = {}

    def _headers(self, access_token: str | None = None) -> dict[str, str]:
        h = {
            "apikey": self.anon_key,
            "Content-Type": "application/json",
        }
        if access_token:
            h["Authorization"] = f"Bearer {access_token}"
        return h

    def get_user(self, access_token: str) -> dict[str, Any] | None:
        now = time.monotonic()
        cached = self._cache.get(access_token)
        if cached and now < cached[0]:
            return cached[1]
        try:
            r = requests.get(
                f"{self.url}/auth/v1/user",
                headers=self._headers(access_token),
                timeout=8,
            )
            if r.status_code == 200:
                user = r.json()
                self._cache[access_token] = (now + 300, user)
                return user
        except Exception:
            pass
        return None

    def refresh_session(self, refresh_token: str) -> dict[str, Any] | None:
        try:
            r = requests.post(
                f"{self.url}/auth/v1/token?grant_type=refresh_token",
                headers=self._headers(),
                json={"refresh_token": refresh_token},
                timeout=10,
            )
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        return None

    def upsert_device(
        self, access_token: str, user_id: str, email: str, tunnel_url: str, device_name: str
    ) -> bool:
        try:
            headers = self._headers(access_token)
            headers["Prefer"] = "resolution=merge-duplicates"
            r = requests.post(
                f"{self.url}/rest/v1/remote_devices",
                headers=headers,
                json={
                    "user_id": user_id,
                    "email": email,
                    "tunnel_url": tunnel_url,
                    "device_name": device_name,
                    "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                },
                timeout=10,
            )
            return r.status_code in (200, 201, 204)
        except Exception:
            return False
