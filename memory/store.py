"""JSON 文件持久化存储：对话历史 + 定时任务。"""

from __future__ import annotations
import json
import uuid
from pathlib import Path
from datetime import datetime

from app.config import settings
from app.models import (
    Conversation, Message, Task, TaskCreate, Usage,
)

_CONV_FILE = settings.MEMORY_DIR / "conversations.json"
_TASK_FILE = settings.MEMORY_DIR / "tasks.json"


def _load(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def _save(path: Path, data: dict):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ── 对话 ──────────────────────────────────────────────

def list_conversations() -> list[dict]:
    data = _load(_CONV_FILE)
    result = []
    for cid, conv in data.items():
        result.append({
            "id": cid,
            "title": conv.get("title", ""),
            "message_count": len(conv.get("messages", [])),
            "created_at": conv.get("created_at", ""),
            "updated_at": conv.get("updated_at", ""),
        })
    result.sort(key=lambda x: x["updated_at"], reverse=True)
    return result


def get_conversation(cid: str) -> dict | None:
    data = _load(_CONV_FILE)
    return data.get(cid)


def create_conversation(title: str = "") -> str:
    cid = str(uuid.uuid4())[:8]
    now = datetime.now().isoformat()
    data = _load(_CONV_FILE)
    data[cid] = {
        "id": cid,
        "title": title or f"对话-{now[:16]}",
        "messages": [],
        "created_at": now,
        "updated_at": now,
    }
    _save(_CONV_FILE, data)
    return cid


def append_message(cid: str, role: str, content: str, model: str = "", usage: dict | None = None, tool_calls: list | None = None):
    data = _load(_CONV_FILE)
    if cid not in data:
        return
    now = datetime.now().isoformat()
    msg = {"role": role, "content": content, "timestamp": now}
    if model:
        msg["model"] = model
    if usage:
        msg["usage"] = usage
    if tool_calls:
        msg["tool_calls"] = tool_calls
    data[cid]["messages"].append(msg)
    data[cid]["updated_at"] = now
    # 自动生成标题：取第一条用户消息前 30 字
    if role == "user" and not data[cid].get("title", "").startswith("对话-"):
        data[cid]["title"] = content[:30]
    _save(_CONV_FILE, data)


def get_history(cid: str, limit: int = 50) -> list[dict]:
    data = _load(_CONV_FILE)
    conv = data.get(cid)
    if not conv:
        return []
    msgs = conv.get("messages", [])
    return msgs[-limit:]


def delete_conversation(cid: str) -> bool:
    data = _load(_CONV_FILE)
    if cid in data:
        del data[cid]
        _save(_CONV_FILE, data)
        return True
    return False


# ── 定时任务 ──────────────────────────────────────────

def list_tasks() -> list[dict]:
    data = _load(_TASK_FILE)
    return list(data.values())


def get_task(task_id: str) -> dict | None:
    data = _load(_TASK_FILE)
    return data.get(task_id)


def create_task(req: TaskCreate) -> dict:
    task_id = str(uuid.uuid4())[:8]
    task = {
        "id": task_id,
        "name": req.name,
        "type": req.type.value,
        "schedule": req.schedule,
        "prompt": req.prompt,
        "enabled": req.enabled,
        "created_at": datetime.now().isoformat(),
        "last_run": None,
    }
    data = _load(_TASK_FILE)
    data[task_id] = task
    _save(_TASK_FILE, data)
    return task


def delete_task(task_id: str) -> bool:
    data = _load(_TASK_FILE)
    if task_id in data:
        del data[task_id]
        _save(_TASK_FILE, data)
        return True
    return False


def update_task(task_id: str, updates: dict) -> dict | None:
    data = _load(_TASK_FILE)
    if task_id not in data:
        return None
    data[task_id].update(updates)
    _save(_TASK_FILE, data)
    return data[task_id]
