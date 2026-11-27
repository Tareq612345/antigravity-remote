import os
import subprocess
import sys
import threading
import time
import webbrowser

import pystray
import uvicorn
import webview
from PIL import Image, ImageDraw

import server


class DesktopBridge:
    def open_url(self, url: str) -> None:
        webbrowser.open(url)

    def get_token(self) -> str:
        return server.config["token"]


def create_tray_icon_image() -> Image.Image:
    icon_path = server.STATIC_DIR / "app.png"
    if icon_path.exists():
        return Image.open(icon_path).convert("RGBA").resize((64, 64), Image.Resampling.LANCZOS)
    img = Image.new("RGBA", (64, 64), (13, 13, 13, 255))
    draw = ImageDraw.Draw(img)
    draw.ellipse((8, 8, 56, 56), fill=(16, 163, 127, 255))
    draw.ellipse((22, 22, 42, 42), fill=(255, 255, 255, 255))
    return img


def run_uvicorn() -> None:
    port = int(server.config.get("port", 8765))
    host = server.config.get("bind_host", "127.0.0.1")
    uvicorn.run(server.app, host=host, port=port, log_level="warning", access_log=False)


def cleanup_stale_instances() -> None:
    """Ensure only one AntigravityRemote instance holds port 8765."""
    my_pid = os.getpid()
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        subprocess.run(
            ["taskkill", "/F", "/FI", f"PID ne {my_pid}", "/IM", "AntigravityRemote.exe"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
            check=False,
        )
        subprocess.run(
            ["taskkill", "/F", "/IM", "cloudflared.exe"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
            check=False,
        )
        time.sleep(0.4)


def main() -> None:
    cleanup_stale_instances()
    srv_thread = threading.Thread(target=run_uvicorn, daemon=True)
    srv_thread.start()
    time.sleep(0.8)

    port = int(server.config.get("port", 8765))
    desktop_url = f"http://127.0.0.1:{port}/desktop"

    bridge = DesktopBridge()
    window = webview.create_window(
        "Antigravity Remote",
        desktop_url,
        width=520,
        height=680,
        resizable=True,
        js_api=bridge,
    )

    tray_icon: pystray.Icon | None = None

    def on_closing() -> bool:
        # Minimize to tray instead of exiting
        window.hide()
        return False

    window.events.closing += on_closing

    def show_window(icon, item) -> None:
        window.show()

    def open_mobile_site(icon, item) -> None:
        webbrowser.open("https://antigravity-remote-8fi.pages.dev/")

    def quit_app(icon, item) -> None:
        if tray_icon:
            tray_icon.stop()
        server.tunnel_manager.stop()
        server.sleep_manager.stop()
        window.destroy()
        sys.exit(0)

    tray_icon = pystray.Icon(
        "AntigravityRemote",
        create_tray_icon_image(),
        "Antigravity Remote",
        menu=pystray.Menu(
            pystray.MenuItem("Show Control Panel", show_window, default=True),
            pystray.MenuItem("Open Mobile Web", open_mobile_site),
            pystray.MenuItem("Quit", quit_app),
        ),
    )

    threading.Thread(target=tray_icon.run, daemon=True).start()
    webview.start()


if __name__ == "__main__":
    main()
