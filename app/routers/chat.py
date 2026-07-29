"""对话 API：POST /chat 支持同步和 SSE 流式。"""

from __future__ import annotations
import json
import logging

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.models import ChatRequest, ChatResponse, ErrorResponse, ToolCall
from app.agent import run_cli_collect, run_cli_stream
import memory.store as store

logger = logging.getLogger("router.chat")
router = APIRouter(prefix="/chat", tags=["chat"])


@router.post(
    "",
    response_model=ChatResponse,
    responses={500: {"model": ErrorResponse}},
    summary="发送消息给助理",
)
async def chat(req: ChatRequest):
    cid = req.conversation_id or store.create_conversation()
    store.append_message(cid, "user", req.message)

    if req.stream:
        return StreamingResponse(
            _stream_response(req, cid),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Conversation-Id": cid},
        )

    # 同步调用 — 收集工具调用详情
    try:
        result = await run_cli_collect(
            prompt=req.message,
            model=req.model,
            max_turns=req.max_turns,
            work_dir=req.work_dir,
            system_prompt=req.system_prompt,
        )
    except Exception as e:
        logger.exception("对话失败")
        return ChatResponse(
            conversation_id=cid,
            message=f"[错误] {e}",
        )

    result_text = result["result"]
    tool_calls = [
        ToolCall(**tc) if "type" in tc else ToolCall(type="call", **tc)
        for tc in result["tool_calls"]
    ]
    model = result["model"]
    usage = result["usage"]

    store.append_message(
        cid, "assistant", result_text,
        model=model,
        usage=usage.model_dump() if usage else None,
        tool_calls=[tc.model_dump() for tc in tool_calls],
    )

    return ChatResponse(
        conversation_id=cid,
        message=result_text,
        model=model,
        usage=usage,
        tool_calls=tool_calls,
    )


async def _stream_response(req: ChatRequest, cid: str):
    """SSE 流式：逐事件推送 CLI 输出。"""
    full_text = ""
    model = ""
    tool_calls = []

    try:
        async for event in run_cli_stream(
            prompt=req.message,
            model=req.model,
            max_turns=req.max_turns,
            work_dir=req.work_dir,
            system_prompt=req.system_prompt,
        ):
            yield event
            if event.startswith("data: ") and event.strip() != "data: [DONE]":
                try:
                    ev = json.loads(event[6:])
                    etype = ev.get("type", "")
                    if etype == "result":
                        full_text = ev.get("result", "")
                        model = ev.get("model", "")
                except json.JSONDecodeError:
                    pass
    except Exception as e:
        yield f"data: {json.dumps({'type': 'error', 'text': str(e)})}\n\n"

    if full_text:
        store.append_message(cid, "assistant", full_text, model=model)
