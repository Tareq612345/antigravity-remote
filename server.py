import asyncio
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ide_transcript_watcher import is_valid_cid, list_ide_chats, parse_transcript, watch_ide_transcript
from sleep_manager import SleepManager
from tunnel_and_auth import SUPABASE_ANON_KEY, SUPABASE_URL, SupabaseAuthClient, TunnelManager

APP_VERSION = "0.1"
GITHUB_RELEASES_API = "https://api.github.com/repos/Tareq612345/antigravity-remote/releases?per_page=1"

BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
STATIC_DIR = BUNDLE_DIR / "static"
DATA_DIR = Path.home() / ".antigravity-remote"
DATA_DIR.mkdir(parents=True, exist_ok=True)
LEGACY_DIR = Path(__file__).resolve().parent
CONFIG_PATH = (LEGACY_DIR / "config.json") if (LEGACY_DIR / "config.json").exists() else (DATA_DIR / "config.json")
CHATS_PATH = (LEGACY_DIR / "chats.json") if (LEGACY_DIR / "chats.json").exists() else (DATA_DIR / "chats.json")
AUDIT_LOG_PATH = DATA_DIR / "audit.log"
AGY_CONVERSATIONS_DIR = Path.home() / ".gemini" / "antigravity-cli" / "conversations"
DEFAULT_AGY_PATH = str(Path.home() / ".gemini" / "bin" / "agy.exe")

MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB
MAX_WS_CLIENTS = 10
DEFAULT_AGY_MODE = "accept-edits"
ALLOWED_UPLOAD_EXTS = {
    ".txt", ".md", ".json", ".csv", ".log",
    ".png", ".jpg", ".jpeg", ".webp", ".gif",
    ".mp3", ".wav", ".m4a", ".ogg", ".pdf",
}
DEFAULT_ALLOWED_ORIGINS = [
    "https://antigravity-remote-8fi.pages.dev",
    "http://127.0.0.1:8765",
    "http://localhost:8765",
]


def restrict_file_permissions(path: Path) -> None:
    try:
        if os.name == "nt":
            user = os.environ.get("USERNAME") or ""
            if user:
                subprocess.run(
                    ["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
                    check=False,
                )
        else:
            os.chmod(path, 0o600)
    except Exception:
        pass


def audit_log(event: str, detail: str = "") -> None:
    try:
        is_new = not AUDIT_LOG_PATH.exists()
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] {event}: {detail}\n"
        with AUDIT_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(line)
        if is_new:
            restrict_file_permissions(AUDIT_LOG_PATH)
    except Exception:
        pass


def load_or_create_config() -> dict[str, Any]:
    if CONFIG_PATH.exists():
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        cfg.setdefault("agy_path", DEFAULT_AGY_PATH)
        cfg.setdefault("agy_mode", DEFAULT_AGY_MODE)
        cfg.setdefault("bind_host", "127.0.0.1")
        origins = list(cfg.get("allowed_origins") or [])
        for o in DEFAULT_ALLOWED_ORIGINS:
            if o not in origins:
                origins.append(o)
        cfg["allowed_origins"] = origins
        cfg.setdefault("allowed_workspaces", [cfg.get("workspace", str(Path.home() / "Desktop"))])
        return cfg
    default_ws = str(Path.home() / "Desktop")
    cfg = {
        "token": secrets.token_urlsafe(24),
        "agy_path": DEFAULT_AGY_PATH,
        "agy_mode": DEFAULT_AGY_MODE,
        "agy_model": "",
        "agy_effort": "",
        "workspace": default_ws,
        "allowed_workspaces": [default_ws],
        "allowed_origins": DEFAULT_ALLOWED_ORIGINS,
        "bind_host": "127.0.0.1",
        "sleep_timeout_hours": 7,
        "keep_display_on": False,
        "port": 8765,
    }
    save_config(cfg)
    return cfg


def save_config(cfg: dict[str, Any]) -> None:
    is_new = not CONFIG_PATH.exists()
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    if is_new:
        restrict_file_permissions(CONFIG_PATH)


def load_chats_store() -> dict[str, Any]:
    if CHATS_PATH.exists():
        try:
            return json.loads(CHATS_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_chats_store(store: dict[str, Any]) -> None:
    is_new = not CHATS_PATH.exists()
    CHATS_PATH.write_text(json.dumps(store, indent=2, ensure_ascii=False), encoding="utf-8")
    if is_new:
        restrict_file_permissions(CHATS_PATH)


config = load_or_create_config()
sleep_manager = SleepManager(
    default_timeout_seconds=int(config.get("sleep_timeout_hours", 7) * 3600),
    keep_display_on=bool(config.get("keep_display_on", False)),
)
sb_auth = SupabaseAuthClient()
tunnel_manager = TunnelManager(local_port=int(config.get("port", 8765)), data_dir=DATA_DIR)


class RateLimiter:
    def __init__(self, max_requests: int, window_seconds: int = 60) -> None:
        self.max_requests = max_requests
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> bool:
        now = time.monotonic()
        q = self._hits[key]
        while q and now - q[0] > self.window:
            q.popleft()
        if len(q) >= self.max_requests:
            return False
        q.append(now)
        return True


api_limiter = RateLimiter(max_requests=90, window_seconds=60)
auth_limiter = RateLimiter(max_requests=10, window_seconds=60)


class HubState:
    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()
        self.chats_store: dict[str, Any] = load_chats_store()
        self.history: list[dict[str, Any]] = []
        self.conversation_id: str | None = None
        self.queue: list[dict[str, Any]] = []
        self.queue_event = asyncio.Event()
        self.current_proc: asyncio.subprocess.Process | None = None
        self.busy = False
        self._worker_task: asyncio.Task[None] | None = None
        self._ide_watch_task: asyncio.Task[None] | None = None
        self.ide_mirror_id: str | None = None
        self.pairing_codes: dict[str, tuple[str, float]] = {}
        self.active_ws_connections: int = 0
        self.models_cache: tuple[float, list[dict[str, str]]] | None = None
        self.skills_cache: tuple[float, list[dict[str, str]]] | None = None
        self.update_cache: tuple[float, dict[str, Any]] | None = None
        self.usage_stats: dict[str, Any] = {
            "turns": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "thinking_tokens": 0,
            "cache_read_tokens": 0,
            "total_tokens": 0,
            "by_model": {},
        }

    def issue_pairing_code(self) -> tuple[str, str]:
        now = time.monotonic()
        self.pairing_codes = {k: v for k, v in self.pairing_codes.items() if v[1] > now}
        code = secrets.token_urlsafe(18)
        cookie_secret = secrets.token_urlsafe(18)
        self.pairing_codes[code] = (cookie_secret, now + 300)  # 5 minutes
        return code, cookie_secret

    def consume_pairing_code(self, code: str | None, cookie_secret: str | None) -> bool:
        if not code or not cookie_secret:
            return False
        entry = self.pairing_codes.get(code)
        if not entry:
            return False
        expected_cookie, exp = entry
        if exp <= time.monotonic():
            self.pairing_codes.pop(code, None)
            return False
        if not secrets.compare_digest(cookie_secret, expected_cookie):
            return False
        self.pairing_codes.pop(code, None)
        return True

    async def broadcast(self, payload: dict[str, Any]) -> None:
        dead: list[WebSocket] = []
        text = json.dumps(payload, ensure_ascii=False)
        for ws in list(self.clients):
            try:
                await ws.send_text(text)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)

    def persist_current_chat(self) -> None:
        if not self.conversation_id or not is_valid_cid(self.conversation_id):
            return
        title = "New Chat"
        for m in self.history:
            if m.get("role") == "user" and m.get("text"):
                title = m["text"][:50]
                break
        self.chats_store[self.conversation_id] = {
            "id": self.conversation_id,
            "title": title,
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "messages": self.history[-100:],
        }
        save_chats_store(self.chats_store)

    def enqueue(self, item: dict[str, Any]) -> int:
        self.queue.append(item)
        self.queue_event.set()
        return len(self.queue)

    async def worker_loop(self) -> None:
        while True:
            await self.queue_event.wait()
            while self.queue:
                item = self.queue.pop(0)
                await self.broadcast({"type": "queue", "count": len(self.queue)})
                await handle_agy_turn(item["prompt"], item["msg_id"])
            self.queue_event.clear()


hub = HubState()


def sync_tunnel_to_supabase(public_url: str | None = None, access_token: str | None = None) -> bool:
    url = public_url or tunnel_manager.public_url
    if not url:
        return False
    tok = access_token
    user: dict[str, Any] = {}
    if not tok:
        rt = config.get("supabase_refresh_token")
        if not rt:
            return False
        sess = sb_auth.refresh_session(rt)
        if not sess or not sess.get("access_token"):
            audit_log("supabase_sync_failed", "refresh_token_expired")
            return False
        config["supabase_refresh_token"] = sess.get("refresh_token", rt)
        tok = sess["access_token"]
        user = sess.get("user") or {}
    if not user:
        user = sb_auth.get_user(tok) or {}
    if user.get("id") and user.get("email"):
        config["owner_user_id"] = user["id"]
        config["owner_email"] = user["email"]
        save_config(config)
        ok = sb_auth.upsert_device(
            tok,
            user["id"],
            user["email"],
            url,
            socket.gethostname(),
        )
        audit_log("supabase_sync", f"email={user['email']} ok={ok}")
        return ok
    return False


@asynccontextmanager
async def lifespan(_: FastAPI):
    sleep_manager.start()
    tunnel_manager.start(on_url_ready=sync_tunnel_to_supabase)
    hub._worker_task = asyncio.create_task(hub.worker_loop())
    audit_log("server_start", f"bind={config.get('bind_host', '127.0.0.1')}:{config.get('port', 8765)}")
    yield
    if hub._worker_task:
        hub._worker_task.cancel()
    if hub._ide_watch_task:
        hub._ide_watch_task.cancel()
    if hub.current_proc and hub.current_proc.returncode is None:
        hub.current_proc.terminate()
    tunnel_manager.stop()
    sleep_manager.stop()


app = FastAPI(title="Antigravity Remote", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.get("allowed_origins", DEFAULT_ALLOWED_ORIGINS),
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type", "X-Access-Token", "X-Supabase-Token"],
)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def client_ip(request: Request) -> str:
    peer = (request.client.host if request.client else "") or "unknown"
    if peer in ("127.0.0.1", "::1", "localhost") and request.headers.get("cf-ray"):
        cf_ip = (request.headers.get("cf-connecting-ip") or "").strip()
        if cf_ip:
            return cf_ip
    return peer


def is_local_client(request: Request) -> bool:
    # Only trust direct loopback connections (ignore X-Forwarded-For from tunnels)
    host = client_ip(request)
    has_proxy_hdr = bool(request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for"))
    return host in ("127.0.0.1", "::1", "localhost", "testclient") and not has_proxy_hdr


def check_credentials(local_token: str | None, sb_token: str | None) -> bool:
    if local_token and secrets.compare_digest(local_token, config["token"]):
        return True
    if sb_token and config.get("owner_email"):
        user = sb_auth.get_user(sb_token)
        if user and (user.get("email") or "").lower() == config["owner_email"].lower():
            return True
    return False


def verify_request(request: Request) -> None:
    ip = client_ip(request)
    if not api_limiter.check(ip):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    auth_hdr = request.headers.get("authorization") or ""
    bearer = auth_hdr[7:].strip() if auth_hdr.lower().startswith("bearer ") else None
    local_tok = request.headers.get("x-access-token") or bearer
    sb_tok = request.headers.get("x-supabase-token") or bearer

    if check_credentials(local_tok, sb_tok):
        return

    if not auth_limiter.check(f"fail:{ip}"):
        raise HTTPException(status_code=429, detail="Too many failed auth attempts")
    audit_log("auth_failure", f"ip={ip} path={request.url.path}")
    raise HTTPException(status_code=401, detail="Unauthorized")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/desktop")
async def desktop_ui() -> FileResponse:
    return FileResponse(STATIC_DIR / "desktop.html")


@app.get("/pc-login")
async def pc_login_page(request: Request) -> HTMLResponse:
    if not is_local_client(request):
        raise HTTPException(status_code=403, detail="PC pairing is only allowed from localhost")
    pair_code, cookie_secret = hub.issue_pairing_code()
    html = f"""<!DOCTYPE html><html lang="en" dir="ltr"><head><meta charset="utf-8"><title>Link PC — Antigravity Remote</title>
    <script src="https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2"></script>
    <style>
      body{{background:#0d0d0d;color:#ececec;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;}}
      .card{{background:#171717;border:1px solid rgba(255,255,255,.1);padding:28px;border-radius:16px;width:90%;max-width:400px;text-align:center;display:flex;flex-direction:column;gap:14px;align-items:center;}}
      .spinner{{width:28px;height:28px;border:3px solid rgba(255,255,255,.15);border-top-color:#10a37f;border-radius:50%;animation:spin .8s linear infinite;display:none;}}
      @keyframes spin{{to{{transform:rotate(360deg)}}}}
      .btn{{padding:10px 20px;border-radius:10px;border:0;background:#fff;color:#000;font-weight:600;cursor:pointer;font-size:14px;}}
      .btn.ok{{background:#10a37f;color:#fff;}}
      .link-btn{{background:transparent;border:1px solid rgba(255,255,255,.15);color:#9b9b9b;padding:7px 14px;border-radius:8px;cursor:pointer;font-size:12.5px;}}
      .link-btn:hover{{color:#fff;border-color:rgba(255,255,255,.35);}}
    </style></head>
    <body>
    <div class="card">
      <h2 id="title" style="margin:0;font-size:20px;">Link This PC to Your Account</h2>
      <div id="spinner" class="spinner"></div>
      <p id="msg" style="color:#9b9b9b;font-size:13.5px;margin:0;line-height:1.5;">Sign in with the same Google account you use on your mobile browser.</p>
      <button id="loginBtn" class="btn" onclick="loginGoogle()">Sign in with Google</button>
      <button id="confirmBtn" class="btn ok" style="display:none;" onclick="confirmLink()">Confirm &amp; Link This PC</button>
      <div style="width:100%;border-top:1px solid rgba(255,255,255,.08);padding-top:12px;display:none;" id="footerActions">
        <button class="link-btn" onclick="switchAccount()">Switch Account / Sign Out</button>
      </div>
    </div>
    <script>
      const PAIR_CODE = "{pair_code}";
      const sb = supabase.createClient("{SUPABASE_URL}", "{SUPABASE_ANON_KEY}");
      let currentSession = null;
      const setBusy = (on, text) => {{
        document.getElementById('spinner').style.display = on ? 'block' : 'none';
        if (text) document.getElementById('msg').textContent = text;
      }};
      async function loginGoogle() {{
        document.getElementById('loginBtn').style.display = 'none';
        setBusy(true, 'Redirecting to Google Sign-In...');
        await sb.auth.signInWithOAuth({{ provider: 'google', options: {{ redirectTo: window.location.origin + '/pc-login' }} }});
      }}
      async function confirmLink() {{
        if (!currentSession) return;
        document.getElementById('confirmBtn').style.display = 'none';
        setBusy(true, 'Linking PC to ' + currentSession.user.email + '...');
        const r = await fetch('/api/pc-link', {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify({{ pair_code: PAIR_CODE, access_token: currentSession.access_token, refresh_token: currentSession.refresh_token }})
        }});
        if (r.ok) {{
          setBusy(false, '✅ PC linked successfully to ' + currentSession.user.email + '. You can close this tab.');
          document.getElementById('title').textContent = 'PC Connected';
        }} else {{
          setBusy(false, 'Failed to link PC. Please refresh and try again.');
          document.getElementById('confirmBtn').style.display = 'inline-block';
        }}
      }}
      async function switchAccount() {{
        setBusy(true, 'Signing out...');
        await sb.auth.signOut({{ scope: 'local' }});
        location.href = '/pc-login';
      }}
      (async () => {{
        const {{ data: {{ session }} }} = await sb.auth.getSession();
        if (session) {{
          currentSession = session;
          document.getElementById('loginBtn').style.display = 'none';
          document.getElementById('confirmBtn').style.display = 'inline-block';
          document.getElementById('footerActions').style.display = 'block';
          setBusy(false, 'Signed in as ' + session.user.email + '. Click Confirm to link this PC.');
        }}
      }})();
    </script></body></html>"""
    resp = HTMLResponse(html)
    resp.set_cookie(key="ag_pair", value=cookie_secret, httponly=True, samesite="strict", max_age=300)
    return resp


class PcLinkBody(BaseModel):
    pair_code: str
    access_token: str
    refresh_token: str


@app.post("/api/pc-link")
async def pc_link(request: Request, body: PcLinkBody) -> JSONResponse:
    ip = client_ip(request)
    if not auth_limiter.check(f"pclink:{ip}"):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    if not is_local_client(request):
        raise HTTPException(status_code=403, detail="PC pairing is only allowed from localhost")
    cookie_secret = request.cookies.get("ag_pair")
    if not hub.consume_pairing_code(body.pair_code, cookie_secret):
        audit_log("pc_link_failed", "invalid_or_expired_pair_code")
        raise HTTPException(status_code=401, detail="Invalid or expired pairing code")

    user = sb_auth.get_user(body.access_token)
    if not user or not user.get("id") or not user.get("email"):
        raise HTTPException(status_code=401, detail="Invalid Supabase session")
    config["supabase_refresh_token"] = body.refresh_token
    config["owner_user_id"] = user["id"]
    config["owner_email"] = user["email"]
    save_config(config)
    synced = sync_tunnel_to_supabase(access_token=body.access_token)
    audit_log("pc_link_success", f"email={user['email']} synced={synced}")
    resp = JSONResponse({"email": user["email"], "synced": synced})
    resp.delete_cookie("ag_pair")
    return resp


@app.post("/api/logout")
async def api_logout(request: Request) -> JSONResponse:
    verify_request(request)
    auth_hdr = request.headers.get("authorization") or ""
    bearer = auth_hdr[7:].strip() if auth_hdr.lower().startswith("bearer ") else None
    sb_tok = request.headers.get("x-supabase-token") or bearer
    sb_auth.invalidate(sb_tok)
    audit_log("client_logout", f"ip={client_ip(request)}")
    return JSONResponse({"ok": True})


@app.get("/api/status")
async def get_status(request: Request) -> JSONResponse:
    verify_request(request)
    st = sleep_manager.status()
    payload: dict[str, Any] = {
        "agy_mode": config.get("agy_mode", DEFAULT_AGY_MODE),
        "agy_model": config.get("agy_model", ""),
        "agy_effort": config.get("agy_effort", ""),
        "usage": hub.usage_stats,
        "busy": hub.busy,
        "queue_count": len(hub.queue),
        "sleep": {
            "active": st.active,
            "keep_display_on": st.keep_display_on,
            "remaining_seconds": st.remaining_seconds,
            "timeout_seconds": st.timeout_seconds,
        },
    }
    if is_local_client(request):
        payload["workspace"] = config["workspace"]
        payload["owner_email"] = config.get("owner_email")
        payload["tunnel_url"] = tunnel_manager.public_url
        payload["agy_installed"] = Path(config.get("agy_path", DEFAULT_AGY_PATH)).exists()
        payload["app_version"] = APP_VERSION
    return JSONResponse(payload)


def _ver_tuple(v: str) -> tuple[int, ...]:
    parts = [int(x) for x in re.findall(r"\d+", v)]
    return tuple(parts) if parts else (0,)


@app.get("/api/check-update")
async def check_update(request: Request) -> JSONResponse:
    verify_request(request)
    if not is_local_client(request):
        raise HTTPException(status_code=403, detail="Localhost only")
    now = time.monotonic()
    if hub.update_cache and now < hub.update_cache[0]:
        return JSONResponse(hub.update_cache[1])

    result: dict[str, Any] = {
        "update_available": False,
        "current": APP_VERSION,
        "latest": APP_VERSION,
        "url": "https://github.com/Tareq612345/antigravity-remote/releases",
    }
    try:
        import requests as _req
        r = _req.get(GITHUB_RELEASES_API, timeout=6, headers={"Accept": "application/vnd.github+json"})
        if r.status_code == 200 and r.json():
            rel = r.json()[0]
            tag = (rel.get("tag_name") or "").strip()
            if tag and _ver_tuple(tag) > _ver_tuple(APP_VERSION):
                result = {
                    "update_available": True,
                    "current": APP_VERSION,
                    "latest": tag,
                    "url": rel.get("html_url") or result["url"],
                }
        hub.update_cache = (now + 3600, result)
    except Exception:
        pass
    return JSONResponse(result)


@app.post("/api/install-agy")
async def install_agy(request: Request) -> JSONResponse:
    verify_request(request)
    if not is_local_client(request):
        raise HTTPException(status_code=403, detail="Localhost only")
    agy_path = Path(config.get("agy_path", DEFAULT_AGY_PATH))
    if not agy_path.exists():
        proc = await asyncio.create_subprocess_exec(
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            "irm https://antigravity.google/cli/install.ps1 | iex",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=getattr(asyncio.subprocess, "CREATE_NO_WINDOW", 0x08000000),
        )
        _, err = await asyncio.wait_for(proc.communicate(), timeout=120.0)
        if proc.returncode != 0 or not agy_path.exists():
            raise HTTPException(status_code=500, detail=err.decode("utf-8", errors="replace")[:300] or "Install failed")
        audit_log("agy_installed", "via_desktop_app")
    # Launch interactive agy window for first-time sign-in if requested
    subprocess.Popen(["cmd.exe", "/c", "start", "", str(agy_path)], shell=False)
    return JSONResponse({"installed": True, "path": str(agy_path)})


@app.get("/api/models")
async def get_models(request: Request) -> JSONResponse:
    verify_request(request)
    now = time.monotonic()
    models: list[dict[str, str]] = []
    if hub.models_cache and now < hub.models_cache[0]:
        models = hub.models_cache[1]
    else:
        agy_exe = config.get("agy_path", DEFAULT_AGY_PATH)
        if Path(agy_exe).exists():
            try:
                proc = await asyncio.create_subprocess_exec(
                    agy_exe,
                    "models",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    creationflags=getattr(asyncio.subprocess, "CREATE_NO_WINDOW", 0x08000000),
                )
                out, _ = await asyncio.wait_for(proc.communicate(), timeout=12.0)
                for line in out.decode("utf-8", errors="replace").splitlines():
                    if "\t" in line:
                        mid, mname = line.split("\t", 1)
                        models.append({"id": mid.strip(), "name": mname.strip()})
                if models:
                    hub.models_cache = (now + 300, models)
            except Exception:
                pass
    return JSONResponse(
        {
            "models": models,
            "current_model": config.get("agy_model", ""),
            "current_effort": config.get("agy_effort", ""),
            "usage": hub.usage_stats,
        }
    )


class ModelUpdate(BaseModel):
    model: str = ""
    effort: str = ""


@app.post("/api/model")
async def update_model(request: Request, body: ModelUpdate) -> JSONResponse:
    verify_request(request)
    if body.model and not re.fullmatch(r"[a-zA-Z0-9._-]{1,64}", body.model):
        raise HTTPException(status_code=400, detail="Invalid model id")
    if body.effort not in ("", "low", "medium", "high", "xhigh", "max"):
        raise HTTPException(status_code=400, detail="Invalid effort level")
    config["agy_model"] = body.model
    config["agy_effort"] = body.effort
    save_config(config)
    audit_log("model_update", f"model={body.model or 'default'} effort={body.effort or 'default'}")
    return JSONResponse({"model": config["agy_model"], "effort": config["agy_effort"]})


BUILTIN_SLASH_COMMANDS = [
    {"name": "btw", "desc": "Ask a quick question without interrupting the main conversation.", "kind": "cmd"},
    {"name": "goal", "desc": "Run until the specified goal is completely finished.", "kind": "cmd"},
    {"name": "compact", "desc": "Compact the conversation now to free up context.", "kind": "cmd"},
    {"name": "schedule", "desc": "Run an instruction on a recurring schedule or as a timer.", "kind": "cmd"},
    {"name": "browser", "desc": "Invoke a browser agent for web tasks.", "kind": "cmd"},
    {"name": "plan", "desc": "Plan carefully before executing a task.", "kind": "cmd"},
    {"name": "grill-me", "desc": "Interview me to align on a plan.", "kind": "cmd"},
    {"name": "teamwork", "desc": "Invoke a team of agents to autonomously tackle large projects.", "kind": "cmd"},
    {"name": "learn", "desc": "Reflect on recent successes or corrections to capture reusable skills.", "kind": "cmd"},
    {"name": "boost", "desc": "Invoke the Boost multi-agent orchestrator for complex tasks.", "kind": "cmd"},
]


@app.get("/api/skills")
async def get_skills(request: Request) -> JSONResponse:
    verify_request(request)
    now = time.monotonic()
    if hub.skills_cache and now < hub.skills_cache[0]:
        return JSONResponse({"items": hub.skills_cache[1]})

    items = list(BUILTIN_SLASH_COMMANDS)
    seen = {x["name"] for x in items}
    skill_roots = [
        Path.home() / ".gemini" / "antigravity" / "builtin" / "skills",
        Path.home() / ".gemini" / "antigravity" / "skills",
    ]
    for root in skill_roots:
        if not root.exists():
            continue
        for smd in sorted(root.glob("*/SKILL.md"))[:40]:
            try:
                head = smd.read_text(encoding="utf-8", errors="replace")[:1500]
                m_name = re.search(r"^name:\s*(.+)$", head, re.MULTILINE)
                m_desc = re.search(r"^description:\s*>?-?\s*(.+)$", head, re.MULTILINE)
                sname = (m_name.group(1).strip().strip('"\'') if m_name else smd.parent.name)
                sdesc = (m_desc.group(1).strip().strip('"\'') if m_desc else f"Skill: {sname}")[:120]
                if sname and sname not in seen:
                    seen.add(sname)
                    items.append({"name": sname, "desc": sdesc, "kind": "skill"})
            except Exception:
                continue
    hub.skills_cache = (now + 300, items)
    return JSONResponse({"items": items})


class WorkspaceUpdate(BaseModel):
    workspace: str
    agy_mode: str | None = None


@app.post("/api/workspace")
async def update_workspace(request: Request, body: WorkspaceUpdate) -> JSONResponse:
    verify_request(request)
    target_real = os.path.realpath(os.path.expanduser(body.workspace))
    if not os.path.isdir(target_real):
        raise HTTPException(status_code=400, detail="Directory does not exist")

    allowed_list = [os.path.realpath(p) for p in config.get("allowed_workspaces", [])]
    if not is_local_client(request) and target_real not in allowed_list:
        audit_log("workspace_blocked", f"ip={client_ip(request)} target={target_real}")
        raise HTTPException(status_code=403, detail="Workspace not in allowlist; add it from the local PC")

    config["workspace"] = target_real
    if target_real not in allowed_list:
        config.setdefault("allowed_workspaces", []).append(target_real)
    if body.agy_mode in ("accept-edits", "plan"):
        config["agy_mode"] = body.agy_mode
    save_config(config)
    audit_log("workspace_update", f"workspace={target_real} mode={config['agy_mode']}")
    return JSONResponse({"workspace": config["workspace"], "agy_mode": config["agy_mode"]})


class SleepUpdate(BaseModel):
    extend_hours: float | None = None
    keep_display_on: bool | None = None


@app.post("/api/sleep")
async def update_sleep(request: Request, body: SleepUpdate) -> JSONResponse:
    verify_request(request)
    if body.keep_display_on is not None:
        sleep_manager.set_keep_display(body.keep_display_on)
    if body.extend_hours is not None:
        hours = max(0.1, min(24.0, body.extend_hours))
        sleep_manager.touch(int(hours * 3600))
    else:
        sleep_manager.touch()
    st = sleep_manager.status()
    return JSONResponse(
        {
            "active": st.active,
            "keep_display_on": st.keep_display_on,
            "remaining_seconds": st.remaining_seconds,
        }
    )


@app.get("/api/chats")
async def list_chats(request: Request) -> JSONResponse:
    verify_request(request)
    items: dict[str, dict[str, Any]] = {}

    for cid, cdata in hub.chats_store.items():
        if not is_valid_cid(cid):
            continue
        items[cid] = {
            "id": cid,
            "title": cdata.get("title", cid[:8]),
            "updated_at": cdata.get("updated_at", ""),
            "source": "remote",
            "mtime": 0.0,
        }

    if AGY_CONVERSATIONS_DIR.exists():
        for db in AGY_CONVERSATIONS_DIR.glob("*.db"):
            cid = db.stem
            if not is_valid_cid(cid):
                continue
            stat = db.stat()
            if cid not in items:
                items[cid] = {
                    "id": cid,
                    "title": f"Antigravity ({cid[:8]})",
                    "updated_at": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
                    "source": "local",
                    "mtime": stat.st_mtime,
                }
            else:
                items[cid]["mtime"] = stat.st_mtime

    for c in list_ide_chats(limit=30):
        if c["id"] not in items:
            items[c["id"]] = c

    sorted_items = sorted(items.values(), key=lambda x: (x["mtime"], x["updated_at"]), reverse=True)[:50]
    return JSONResponse({"current_id": hub.ide_mirror_id or hub.conversation_id, "chats": sorted_items})


class ChatSelect(BaseModel):
    conversation_id: str | None = None
    source: str | None = None


@app.post("/api/chats/select")
async def select_chat(request: Request, body: ChatSelect) -> JSONResponse:
    verify_request(request)
    if hub.busy:
        raise HTTPException(status_code=409, detail="Wait for current response or stop it first")

    cid = body.conversation_id
    if cid is not None and not is_valid_cid(cid):
        raise HTTPException(status_code=400, detail="Invalid conversation ID")

    if hub._ide_watch_task:
        hub._ide_watch_task.cancel()
        hub._ide_watch_task = None
    hub.ide_mirror_id = None

    title = None
    if body.source == "ide" and cid:
        hub.conversation_id = None
        hub.ide_mirror_id = cid
        hub.history = parse_transcript(cid)
        title = f"IDE: {hub.history[0]['text'][:35]}" if hub.history else f"IDE ({cid[:8]})"

        async def _on_ide_msg(msg: dict[str, Any]) -> None:
            hub.history.append(msg)
            await hub.broadcast({"type": "message", "message": msg})

        hub._ide_watch_task = asyncio.create_task(watch_ide_transcript(cid, _on_ide_msg))
    else:
        hub.conversation_id = cid
        if cid and cid in hub.chats_store:
            hub.history = list(hub.chats_store[cid].get("messages", []))
            title = hub.chats_store[cid].get("title")
        else:
            hub.history = []
            if cid:
                title = f"Antigravity ({cid[:8]})"

    await hub.broadcast(
        {
            "type": "history",
            "messages": hub.history,
            "conversation_id": hub.ide_mirror_id or hub.conversation_id,
            "title": title,
        }
    )
    return JSONResponse({"conversation_id": hub.ide_mirror_id or hub.conversation_id, "title": title, "messages": hub.history})


def resolve_safe_path(rel_path: str) -> Path:
    root_real = os.path.realpath(config["workspace"])
    target_real = os.path.realpath(os.path.join(root_real, rel_path))
    if os.path.commonpath([root_real, target_real]) != root_real:
        raise HTTPException(status_code=403, detail="Access outside workspace is forbidden")
    return Path(target_real)


@app.get("/api/files")
async def list_files(request: Request, path: str = "") -> JSONResponse:
    verify_request(request)
    target = resolve_safe_path(path)
    if not target.exists() or not target.is_dir():
        raise HTTPException(status_code=404, detail="Directory not found")
    root_real = Path(os.path.realpath(config["workspace"]))
    items = []
    for entry in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))[:200]:
        items.append(
            {
                "name": entry.name,
                "path": str(Path(os.path.realpath(entry)).relative_to(root_real)).replace("\\", "/"),
                "is_dir": entry.is_dir(),
                "size": entry.stat().st_size if entry.is_file() else 0,
            }
        )
    return JSONResponse({"cwd": path, "workspace": config["workspace"], "items": items})


@app.get("/api/file")
async def read_workspace_file(request: Request, path: str) -> JSONResponse:
    verify_request(request)
    target = resolve_safe_path(path)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    if target.stat().st_size > 256_000:
        raise HTTPException(status_code=413, detail="File too large (>256KB) for preview; use Download")
    content = target.read_text(encoding="utf-8", errors="replace")
    return JSONResponse({"path": path, "content": content})


@app.get("/api/download")
async def download_workspace_file(request: Request, path: str) -> FileResponse:
    verify_request(request)
    target = resolve_safe_path(path)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    audit_log("file_download", f"path={path}")
    return FileResponse(target, filename=target.name)


@app.post("/api/upload")
async def upload_file(request: Request, file: UploadFile = File(...)) -> JSONResponse:
    verify_request(request)
    sleep_manager.touch()

    raw_name = Path(file.filename or "upload.bin").name
    ext = Path(raw_name).suffix.lower()
    if ext not in ALLOWED_UPLOAD_EXTS:
        raise HTTPException(status_code=400, detail=f"File type '{ext}' is not allowed")

    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File exceeds 10 MB limit")

    safe_stem = re.sub(r"[^a-zA-Z0-9._-]", "_", Path(raw_name).stem)[:64] or "file"
    safe_name = f"{safe_stem}_{int(time.time())}{ext}"
    uploads_dir = resolve_safe_path("_uploads")
    uploads_dir.mkdir(parents=True, exist_ok=True)
    dest = resolve_safe_path(f"_uploads/{safe_name}")
    dest.write_bytes(data)
    audit_log("file_upload", f"name={safe_name} bytes={len(data)}")
    return JSONResponse(
        {
            "filename": safe_name,
            "path": str(dest),
            "rel_path": f"_uploads/{safe_name}",
            "size": len(data),
        }
    )


@app.websocket("/ws/chat")
async def ws_chat(websocket: WebSocket) -> None:
    if hub.active_ws_connections >= MAX_WS_CLIENTS:
        await websocket.close(code=4008)
        return
    hub.active_ws_connections += 1
    try:
        await websocket.accept()
        peer = (websocket.client.host if websocket.client else "") or "unknown"
        ip = (
            (websocket.headers.get("cf-connecting-ip") or "").strip()
            if peer in ("127.0.0.1", "::1", "localhost") and websocket.headers.get("cf-ray")
            else peer
        ) or peer
        try:
            auth_raw = await asyncio.wait_for(websocket.receive_text(), timeout=5.0)
            auth_msg = json.loads(auth_raw)
            if auth_msg.get("type") != "auth" or not check_credentials(auth_msg.get("token"), auth_msg.get("sb_token")):
                if not auth_limiter.check(f"ws_fail:{ip}"):
                    audit_log("ws_rate_limited", f"ip={ip}")
                    await websocket.close(code=4029)
                    return
                audit_log("ws_auth_failure", f"ip={ip}")
                await websocket.close(code=4001)
                return
        except Exception:
            if not auth_limiter.check(f"ws_fail:{ip}"):
                await websocket.close(code=4029)
                return
            await websocket.close(code=4001)
            return

        hub.clients.add(websocket)
        sleep_manager.touch()

        await websocket.send_text(
            json.dumps(
                {
                    "type": "history",
                    "messages": hub.history,
                    "conversation_id": hub.conversation_id,
                    "busy": hub.busy,
                    "queue_count": len(hub.queue),
                },
                ensure_ascii=False,
            )
        )

        while True:
            raw = await websocket.receive_text()
            data = json.loads(raw)
            sleep_manager.touch()

            if data.get("type") == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
                continue

            if data.get("type") == "stop":
                if hub.current_proc and hub.current_proc.returncode is None:
                    hub.current_proc.terminate()
                hub.queue.clear()
                await hub.broadcast({"type": "queue", "count": 0})
                audit_log("agy_stop", "user_requested")
                continue

            if data.get("type") == "chat":
                prompt = (data.get("text") or "").strip()
                if not prompt:
                    continue

                open_files = data.get("open_files") or []
                safe_open: list[str] = []
                if isinstance(open_files, list):
                    for fp in open_files[:12]:
                        if isinstance(fp, str) and fp.strip():
                            try:
                                resolve_safe_path(fp.strip())
                                safe_open.append(fp.strip())
                            except Exception:
                                pass

                effective_prompt = prompt
                if safe_open:
                    effective_prompt = f"[Open workspace files: {', '.join(safe_open)}]\n\n{prompt}"

                msg_id = f"m_{int(time.time() * 1000)}"
                is_queued = hub.busy or len(hub.queue) > 0
                user_msg = {"id": msg_id, "role": "user", "text": prompt, "queued": is_queued}
                hub.history.append(user_msg)
                await hub.broadcast({"type": "message", "message": user_msg})
                q_len = hub.enqueue({"prompt": effective_prompt, "msg_id": msg_id})
                await hub.broadcast({"type": "queue", "count": q_len})

    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        hub.clients.discard(websocket)
        hub.active_ws_connections = max(0, hub.active_ws_connections - 1)


async def handle_agy_turn(prompt: str, msg_id: str) -> None:
    hub.busy = True
    for m in hub.history:
        if m.get("id") == msg_id:
            m["queued"] = False
            break
    await hub.broadcast({"type": "status", "busy": True, "active_msg_id": msg_id})

    assistant_text = ""
    thoughts: list[str] = []
    tools: list[dict[str, Any]] = []

    agy_exe = config.get("agy_path", DEFAULT_AGY_PATH)
    workspace = config.get("workspace", str(Path.home() / "Desktop"))

    if not Path(agy_exe).exists():
        await hub.broadcast({"type": "error", "message": f"agy.exe not found at: {agy_exe}"})
        hub.busy = False
        await hub.broadcast({"type": "status", "busy": False})
        return

    mode = config.get("agy_mode", DEFAULT_AGY_MODE)
    if mode not in ("accept-edits", "plan"):
        mode = DEFAULT_AGY_MODE

    args = [
        "-p",
        prompt,
        "--output-format",
        "stream-json",
        "--mode",
        mode,
    ]
    if config.get("agy_model"):
        args.extend(["--model", config["agy_model"]])
    if config.get("agy_effort"):
        args.extend(["--effort", config["agy_effort"]])
    if hub.conversation_id and is_valid_cid(hub.conversation_id):
        args.extend(["--resume", hub.conversation_id])

    audit_log("agy_turn_start", f"mode={mode} prompt_len={len(prompt)}")
    try:
        proc = await asyncio.create_subprocess_exec(
            agy_exe,
            *args,
            cwd=workspace,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=getattr(asyncio.subprocess, "CREATE_NO_WINDOW", 0x08000000),
        )
        hub.current_proc = proc

        assert proc.stdout is not None
        while True:
            line_bytes = await proc.stdout.readline()
            if not line_bytes:
                break
            line = line_bytes.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                continue

            sleep_manager.touch()
            ev_type = evt.get("event")

            if ev_type == "init":
                cid = evt.get("conversation_id")
                if cid and is_valid_cid(cid):
                    hub.conversation_id = cid
                    hub.persist_current_chat()

            elif ev_type == "step_update":
                su = evt.get("step_update", {})
                step_type = su.get("step_type", "")
                delta = su.get("text_delta") or ""

                if step_type in ("thought", "thinking") and delta:
                    thoughts.append(delta)
                    await hub.broadcast({"type": "thought", "text": delta})
                elif step_type == "agent_response" and delta:
                    assistant_text += delta
                    await hub.broadcast({"type": "token", "text": delta})
                elif step_type not in ("user_input", "agent_response", "thought", "thinking", ""):
                    tool_name = su.get("tool_name") or step_type
                    info = {"name": tool_name, "args": str(su.get("state", ""))}
                    if not tools or tools[-1]["name"] != tool_name:
                        tools.append(info)
                        await hub.broadcast({"type": "tool", "tool": info})
                    raw_su = json.dumps(su, ensure_ascii=False)
                    ws_real = os.path.realpath(workspace)
                    for m_path in re.findall(r'"([^"\\]*(?:[\\/][^"\\]+)+)"', raw_su):
                        try:
                            cand = os.path.realpath(os.path.join(ws_real, m_path) if not os.path.isabs(m_path) else m_path)
                            if os.path.commonpath([ws_real, cand]) == ws_real and os.path.isfile(cand):
                                rel = str(Path(cand).relative_to(ws_real)).replace("\\", "/")
                                await hub.broadcast({"type": "file_touched", "path": rel})
                        except Exception:
                            pass

            elif ev_type == "result":
                res = evt.get("result", {})
                usage = res.get("usage") or {}
                if usage:
                    hub.usage_stats["turns"] += 1
                    for k in ("input_tokens", "output_tokens", "thinking_tokens", "cache_read_tokens", "total_tokens"):
                        hub.usage_stats[k] += int(usage.get(k, 0) or 0)
                    mkey = config.get("agy_model") or "default"
                    mstats = hub.usage_stats["by_model"].setdefault(mkey, {"turns": 0, "total_tokens": 0})
                    mstats["turns"] += 1
                    mstats["total_tokens"] += int(usage.get("total_tokens", 0) or 0)
                if not assistant_text and res.get("response"):
                    assistant_text = res["response"]
                    await hub.broadcast({"type": "token", "text": assistant_text})
                if res.get("status") not in (None, "SUCCESS"):
                    await hub.broadcast(
                        {"type": "error", "message": f"Antigravity status: {res.get('status')}"}
                    )

        await proc.wait()
        if proc.returncode != 0 and not assistant_text:
            assert proc.stderr is not None
            err_out = (await proc.stderr.read()).decode("utf-8", errors="replace").strip()
            if err_out:
                await hub.broadcast({"type": "error", "message": err_out[:500]})

        if assistant_text or thoughts or tools:
            final_msg = {
                "role": "assistant",
                "text": assistant_text,
                "thoughts": thoughts,
                "tools": tools,
            }
            hub.history.append(final_msg)
            hub.persist_current_chat()
            await hub.broadcast({"type": "done", "message": final_msg, "conversation_id": hub.conversation_id})
        audit_log("agy_turn_done", f"tools={len(tools)} out_len={len(assistant_text)}")

    except Exception as exc:
        await hub.broadcast({"type": "error", "message": f"Error: {exc}"})
    finally:
        hub.current_proc = None
        hub.busy = False
        await hub.broadcast({"type": "status", "busy": False})
