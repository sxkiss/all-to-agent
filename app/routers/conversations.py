"""会话管理 API：查看、列出、删除对话。"""

from fastapi import APIRouter
from app.models import ErrorResponse

import memory.store as store

router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.get("", summary="列出所有会话")
async def list_conversations():
    return store.list_conversations()


@router.get("/{cid}", summary="获取会话历史")
async def get_conversation(cid: str, limit: int = 50):
    conv = store.get_conversation(cid)
    if not conv:
        return ErrorResponse(error="会话不存在")
    conv["messages"] = conv.get("messages", [])[-limit:]
    return conv


@router.delete("/{cid}", summary="删除会话")
async def delete_conversation(cid: str):
    if store.delete_conversation(cid):
        return {"ok": True, "message": f"已删���会话 {cid}"}
    return ErrorResponse(error="会话不存在")
