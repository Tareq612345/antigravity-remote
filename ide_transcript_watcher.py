import asyncio
import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable

BRAIN_DIR = Path.home() / ".gemini" / "antigravity" / "brain"
USER_REQ_RE = re.compile(r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", re.DOTALL)


def _extract_user_text(raw: str) -> str:
    m = USER_REQ_RE.search(raw or "")
    return m.group(1).strip() if m else (raw or "").strip()


def _parse_entry(obj: dict[str, Any]) -> dict[str, Any] | None:
    etype = obj.get("type")
    if etype == "USER_INPUT":
        text = _extract_user_text(obj.get("content", ""))
        if not text:
            return None
        return {"role": "user", "text": text, "created_at": obj.get("created_at", "")}

    if etype == "PLANNER_RESPONSE":
        content = (obj.get("content") or "").strip()
        tools = [
            {"name": tc.get("name", "tool"), "args": ""}
            for tc in (obj.get("tool_calls") or [])
            if isinstance(tc, dict)
        ]
        if not content and not tools:
            return None
        return {
            "role": "assistant",
            "text": content,
            "tools": tools,
            "created_at": obj.get("created_at", ""),
        }
    return None


def get_transcript_path(conversation_id: str) -> Path:
    return BRAIN_DIR / conversation_id / ".system_generated" / "logs" / "transcript.jsonl"


def list_ide_chats(limit: int = 30) -> list[dict[str, Any]]:
    if not BRAIN_DIR.exists():
        return []
    chats: list[dict[str, Any]] = []
    for tpath in BRAIN_DIR.glob("*/.system_generated/logs/transcript.jsonl"):
        cid = tpath.parent.parent.parent.name
        stat = tpath.stat()
        title = f"IDE ({cid[:8]})"
        try:
            with tpath.open("r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    obj = json.loads(line)
                    if obj.get("type") == "USER_INPUT":
                        txt = _extract_user_text(obj.get("content", ""))
                        if txt:
                            title = txt.splitlines()[0][:50]
                            break
        except Exception:
            pass
        chats.append(
            {
                "id": cid,
                "title": title,
                "updated_at": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
                "source": "ide",
                "mtime": stat.st_mtime,
            }
        )
    chats.sort(key=lambda x: x["mtime"], reverse=True)
    return chats[:limit]


def parse_transcript(conversation_id: str) -> list[dict[str, Any]]:
    tpath = get_transcript_path(conversation_id)
    if not tpath.exists():
        return []
    messages: list[dict[str, Any]] = []
    with tpath.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                parsed = _parse_entry(json.loads(line))
                if parsed:
                    messages.append(parsed)
            except Exception:
                continue
    return messages


async def watch_ide_transcript(
    conversation_id: str,
    on_message: Callable[[dict[str, Any]], Awaitable[None]],
    poll_interval: float = 1.0,
) -> None:
    """Watches an IDE transcript.jsonl file and emits new messages in real time."""
    tpath = get_transcript_path(conversation_id)
    if not tpath.exists():
        return

    offset = tpath.stat().st_size
    while True:
        await asyncio.sleep(poll_interval)
        if not tpath.exists():
            continue
        size = tpath.stat().st_size
        if size <= offset:
            continue
        with tpath.open("r", encoding="utf-8", errors="replace") as f:
            f.seek(offset)
            chunk = f.read()
            offset = f.tell()
        for line in chunk.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                parsed = _parse_entry(json.loads(line))
                if parsed:
                    await on_message(parsed)
            except Exception:
                continue


if __name__ == "__main__":
    # Standalone CLI mode: watch the most recent IDE conversation and print updates
    recent = list_ide_chats(limit=5)
    print("Recent IDE chats:")
    for c in recent:
        print(f"  - {c['id']} | {c['updated_at']} | {c['title']}")
    if recent:
        target_id = recent[0]["id"]
        print(f"\nWatching live: {target_id} ...")

        async def _printer(msg: dict[str, Any]) -> None:
            print(f"[{msg['role']}] {msg['text'][:200]}")

        try:
            asyncio.run(watch_ide_transcript(target_id, _printer))
        except KeyboardInterrupt:
            pass
