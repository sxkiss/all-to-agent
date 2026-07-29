"""健康检查 API。"""

import time
from fastapi import APIRouter
from app.config import settings
import memory.store as store

router = APIRouter(tags=["health"])
_start_time = time.time()


@router.get("/health", summary="健康检查")
async def health():
    return {
        "status": "ok",
        "assistant": settings.ASSISTANT_NAME,
        "model": settings.CLAUDE_MODEL,
        "uptime_seconds": round(time.time() - _start_time, 1),
        "conversations": len(store.list_conversations()),
        "tasks": len(store.list_tasks()),
    }
