import asyncio
import json
import secrets
import socket
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, Query, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ide_transcript_watcher import list_ide_chats, parse_transcript, watch_ide_transcript
from sleep_manager import SleepManager
from tunnel_and_auth import SUPABASE_ANON_KEY, SUPABASE_URL, SupabaseAuthClient, TunnelManager

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
CONFIG_PATH = BASE_DIR / "config.json"
CHATS_PATH = BASE_DIR / "chats.json"
AGY_CONVERSATIONS_DIR = Path.home() / ".gemini" / "antigravity-cli" / "conversations"
DEFAULT_AGY_PATH = str(Path.home() / ".gemini" / "bin" / "agy.exe")


def load_or_create_config() -> dict[str, Any]:
    if CONFIG_PATH.exists():
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        cfg.setdefault("agy_path", DEFAULT_AGY_PATH)
        cfg.setdefault("agy_mode", "accept-edits")
        return cfg
    cfg = {
        "token": secrets.token_urlsafe(16),
        "agy_path": DEFAULT_AGY_PATH,
        "agy_mode": "accept-edits",
        "workspace": str(Path.home() / "Desktop"),
        "sleep_timeout_hours": 7,
        "keep_display_on": False,
        "port": 8765,
    }
    save_config(cfg)
    return cfg


def save_config(cfg: dict[str, Any]) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")


def load_chats_store() -> dict[str, Any]:
    if CHATS_PATH.exists():
        try:
            return json.loads(CHATS_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_chats_store(store: dict[str, Any]) -> None:
    CHATS_PATH.write_text(json.dumps(store, indent=2, ensure_ascii=False), encoding="utf-8")


config = load_or_create_config()
sleep_manager = SleepManager(
    default_timeout_seconds=int(config.get("sleep_timeout_hours", 7) * 3600),
    keep_display_on=bool(config.get("keep_display_on", False)),
)
sb_auth = SupabaseAuthClient()
tunnel_manager = TunnelManager(local_port=int(config.get("port", 8765)))


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
        if not self.conversation_id:
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


def sync_tunnel_to_supabase(public_url: str | None = None) -> bool:
    url = public_url or tunnel_manager.public_url
    rt = config.get("supabase_refresh_token")
    if not url or not rt:
        return False
    sess = sb_auth.refresh_session(rt)
    if not sess or not sess.get("access_token"):
        return False
    config["supabase_refresh_token"] = sess.get("refresh_token", rt)
    user = sess.get("user") or sb_auth.get_user(sess["access_token"]) or {}
    if user.get("id") and user.get("email"):
        config["owner_user_id"] = user["id"]
        config["owner_email"] = user["email"]
        save_config(config)
        ok = sb_auth.upsert_device(
            sess["access_token"],
            user["id"],
            user["email"],
            url,
            socket.gethostname(),
        )
        print(f"[Supabase] Synced device ({user['email']}) -> {url} (ok={ok})")
        return ok
    return False


@asynccontextmanager
async def lifespan(_: FastAPI):
    sleep_manager.start()
    tunnel_manager.start(on_url_ready=sync_tunnel_to_supabase)
    hub._worker_task = asyncio.create_task(hub.worker_loop())
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
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def is_authorized(token: str | None, sb_token: str | None) -> bool:
    if token and token == config["token"]:
        return True
    if sb_token and config.get("owner_email"):
        user = sb_auth.get_user(sb_token)
        if user and (user.get("email") or "").lower() == config["owner_email"].lower():
            return True
    return False


def verify_token(token: str | None, sb_token: str | None = None) -> None:
    if not is_authorized(token, sb_token):
        raise HTTPException(status_code=401, detail="Invalid access token or Google account mismatch")


def get_local_ips() -> list[str]:
    ips: list[str] = []
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
    except Exception:
        pass
    return ips


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/pc-login")
async def pc_login_page() -> HTMLResponse:
    html = f"""<!DOCTYPE html><html lang="ar" dir="rtl"><head><meta charset="utf-8"><title>ربط جهاز الكمبيوتر</title>
    <script src="https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2"></script></head>
    <body style="background:#0d0d0d;color:#fff;font-family:sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;">
    <div style="background:#171717;padding:24px;border-radius:14px;max-width:400px;text-align:center;">
      <h2>ربط هذا الكمبيوتر بحسابك</h2>
      <p id="msg" style="color:#9b9b9b;font-size:14px;">سجل الدخول بنفس حساب جوجل الذي تستخدمه على الموبايل والموقع.</p>
      <button onclick="loginGoogle()" style="padding:10px 18px;border-radius:10px;border:0;background:#fff;color:#000;font-weight:700;cursor:pointer;margin:8px;">تسجيل الدخول بـ Google</button>
    </div>
    <script>
      const sb = supabase.createClient("{SUPABASE_URL}", "{SUPABASE_ANON_KEY}");
      async function loginGoogle() {{
        await sb.auth.signInWithOAuth({{ provider: 'google', options: {{ redirectTo: window.location.origin + '/pc-login' }} }});
      }}
      (async () => {{
        const {{ data: {{ session }} }} = await sb.auth.getSession();
        if (session) {{
          document.getElementById('msg').textContent = 'جاري ربط الجهاز بالحساب: ' + session.user.email + '...';
          const r = await fetch('/api/pc-link?token={config["token"]}', {{
            method: 'POST',
            headers: {{ 'Content-Type': 'application/json' }},
            body: JSON.stringify({{ access_token: session.access_token, refresh_token: session.refresh_token }})
          }});
          if (r.ok) document.getElementById('msg').textContent = '✅ تم ربط الكمبيوتر بنجاح بالحساب: ' + session.user.email;
        }}
      }})();
    </script></body></html>"""
    return HTMLResponse(html)


class PcLinkBody(BaseModel):
    access_token: str
    refresh_token: str


@app.post("/api/pc-link")
async def pc_link(body: PcLinkBody, token: str | None = Query(default=None)) -> JSONResponse:
    verify_token(token)
    user = sb_auth.get_user(body.access_token)
    if not user or not user.get("id") or not user.get("email"):
        raise HTTPException(status_code=401, detail="Invalid Supabase session")
    config["supabase_refresh_token"] = body.refresh_token
    config["owner_user_id"] = user["id"]
    config["owner_email"] = user["email"]
    save_config(config)
    synced = sync_tunnel_to_supabase()
    return JSONResponse({"email": user["email"], "tunnel_url": tunnel_manager.public_url, "synced": synced})


@app.get("/api/status")
async def get_status(token: str | None = Query(default=None), sb_token: str | None = Query(default=None)) -> JSONResponse:
    verify_token(token, sb_token)
    st = sleep_manager.status()
    return JSONResponse(
        {
            "workspace": config["workspace"],
            "agy_mode": config.get("agy_mode", "accept-edits"),
            "owner_email": config.get("owner_email"),
            "tunnel_url": tunnel_manager.public_url,
            "local_ips": get_local_ips(),
            "port": config["port"],
            "agy_exists": Path(config.get("agy_path", DEFAULT_AGY_PATH)).exists(),
            "conversation_id": hub.conversation_id,
            "busy": hub.busy,
            "queue_count": len(hub.queue),
            "sleep": {
                "active": st.active,
                "keep_display_on": st.keep_display_on,
                "remaining_seconds": st.remaining_seconds,
                "timeout_seconds": st.timeout_seconds,
            },
        }
    )


class WorkspaceUpdate(BaseModel):
    workspace: str
    agy_mode: str | None = None


@app.post("/api/workspace")
async def update_workspace(body: WorkspaceUpdate, token: str | None = Query(default=None), sb_token: str | None = Query(default=None)) -> JSONResponse:
    verify_token(token, sb_token)
    p = Path(body.workspace).expanduser()
    if not p.exists() or not p.is_dir():
        raise HTTPException(status_code=400, detail="Directory does not exist")
    config["workspace"] = str(p.resolve())
    if body.agy_mode in ("auto", "accept-edits", "plan"):
        config["agy_mode"] = body.agy_mode
    save_config(config)
    return JSONResponse({"workspace": config["workspace"], "agy_mode": config["agy_mode"]})


class SleepUpdate(BaseModel):
    extend_hours: float | None = None
    keep_display_on: bool | None = None


@app.post("/api/sleep")
async def update_sleep(body: SleepUpdate, token: str | None = Query(default=None), sb_token: str | None = Query(default=None)) -> JSONResponse:
    verify_token(token, sb_token)
    if body.keep_display_on is not None:
        sleep_manager.set_keep_display(body.keep_display_on)
    if body.extend_hours is not None:
        sleep_manager.touch(int(body.extend_hours * 3600))
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
async def list_chats(token: str | None = Query(default=None), sb_token: str | None = Query(default=None)) -> JSONResponse:
    verify_token(token, sb_token)
    items: dict[str, dict[str, Any]] = {}

    for cid, cdata in hub.chats_store.items():
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
async def select_chat(body: ChatSelect, token: str | None = Query(default=None), sb_token: str | None = Query(default=None)) -> JSONResponse:
    verify_token(token, sb_token)
    if hub.busy:
        raise HTTPException(status_code=409, detail="Wait for current response or stop it first")

    if hub._ide_watch_task:
        hub._ide_watch_task.cancel()
        hub._ide_watch_task = None
    hub.ide_mirror_id = None

    cid = body.conversation_id
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
    root = Path(config["workspace"]).resolve()
    target = (root / rel_path).resolve()
    if root != target and root not in target.parents:
        raise HTTPException(status_code=403, detail="Access outside workspace is forbidden")
    return target


@app.get("/api/files")
async def list_files(path: str = "", token: str | None = Query(default=None), sb_token: str | None = Query(default=None)) -> JSONResponse:
    verify_token(token, sb_token)
    target = resolve_safe_path(path)
    if not target.exists() or not target.is_dir():
        raise HTTPException(status_code=404, detail="Directory not found")
    items = []
    for entry in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))[:200]:
        items.append(
            {
                "name": entry.name,
                "path": str(entry.relative_to(Path(config["workspace"]).resolve())).replace("\\", "/"),
                "is_dir": entry.is_dir(),
                "size": entry.stat().st_size if entry.is_file() else 0,
            }
        )
    return JSONResponse({"cwd": path, "workspace": config["workspace"], "items": items})


@app.get("/api/file")
async def read_workspace_file(path: str, token: str | None = Query(default=None), sb_token: str | None = Query(default=None)) -> JSONResponse:
    verify_token(token, sb_token)
    target = resolve_safe_path(path)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    if target.stat().st_size > 256_000:
        raise HTTPException(status_code=413, detail="File too large (>256KB) for preview; use Download")
    content = target.read_text(encoding="utf-8", errors="replace")
    return JSONResponse({"path": path, "content": content})


@app.get("/api/download")
async def download_workspace_file(path: str, token: str | None = Query(default=None), sb_token: str | None = Query(default=None)) -> FileResponse:
    verify_token(token, sb_token)
    target = resolve_safe_path(path)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(target, filename=target.name)


@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...), token: str | None = Query(default=None), sb_token: str | None = Query(default=None)) -> JSONResponse:
    verify_token(token, sb_token)
    sleep_manager.touch()
    uploads_dir = Path(config["workspace"]).resolve() / "_remote_uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    safe_name = Path(file.filename or f"upload_{int(time.time())}").name
    dest = uploads_dir / safe_name
    data = await file.read()
    dest.write_bytes(data)
    return JSONResponse(
        {
            "filename": safe_name,
            "path": str(dest),
            "rel_path": f"_remote_uploads/{safe_name}",
            "size": len(data),
        }
    )


@app.websocket("/ws/chat")
async def ws_chat(websocket: WebSocket, token: str | None = Query(default=None), sb_token: str | None = Query(default=None)) -> None:
    if not is_authorized(token, sb_token):
        await websocket.close(code=4001)
        return

    await websocket.accept()
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

    try:
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
                continue

            if data.get("type") == "chat":
                prompt = (data.get("text") or "").strip()
                if not prompt:
                    continue

                msg_id = f"m_{int(time.time() * 1000)}"
                is_queued = hub.busy or len(hub.queue) > 0
                user_msg = {"id": msg_id, "role": "user", "text": prompt, "queued": is_queued}
                hub.history.append(user_msg)
                await hub.broadcast({"type": "message", "message": user_msg})
                q_len = hub.enqueue({"prompt": prompt, "msg_id": msg_id})
                await hub.broadcast({"type": "queue", "count": q_len})

    except WebSocketDisconnect:
        hub.clients.discard(websocket)
    except Exception:
        hub.clients.discard(websocket)


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

    args = [
        "-p",
        prompt,
        "--output-format",
        "stream-json",
        "--mode",
        config.get("agy_mode", "accept-edits"),
    ]
    if hub.conversation_id:
        args.extend(["--resume", hub.conversation_id])

    try:
        proc = await asyncio.create_subprocess_exec(
            agy_exe,
            *args,
            cwd=workspace,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
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
                if cid:
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

            elif ev_type == "result":
                res = evt.get("result", {})
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

    except Exception as exc:
        await hub.broadcast({"type": "error", "message": f"Error: {exc}"})
    finally:
        hub.current_proc = None
        hub.busy = False
        await hub.broadcast({"type": "status", "busy": False})
