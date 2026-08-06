"""对话 API：POST /chat 支持同步和 SSE 流式，带会话续接。"""

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
    cid = req.conversation_id or store.create_conversation(backend=req.backend)

    # 取 session_id，reset 时清空
    if req.reset_session:
        store.set_session_id(cid, req.backend, "")
        session_id = None
    else:
        session_id = store.get_session_id(cid, req.backend)

    store.append_message(cid, "user", req.message, model=req.model or "", backend=req.backend)

    with open('/tmp/debug_stream.log', 'a') as _df:
        _df.write(f"[CHAT] cid={cid} session={session_id} stream={req.stream} backend={req.backend}\n")
        _df.flush()

    if req.stream:
        return StreamingResponse(
            _stream_response(req, cid, session_id),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Conversation-Id": cid,
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    try:
        result = await run_cli_collect(
            prompt=req.message,
            backend=req.backend,
            model=req.model,
            max_turns=req.max_turns,
            work_dir=req.work_dir,
            session_id=session_id,
        )
    except Exception as e:
        logger.exception("对话失败")
        return ChatResponse(conversation_id=cid, message=f"[错误] {e}")

    # 保存 session_id 用于下次续接（可能因上下文降级而更新为新 session）
    if result.get("session_id"):
        store.set_session_id(cid, req.backend, result["session_id"])

    result_text = result["result"]
    tool_calls = [ToolCall(**tc) for tc in result["tool_calls"]]
    usage = result["usage"]

    store.append_message(
        cid, "assistant", result_text,
        model=req.model or "",
        usage=usage.model_dump() if usage else None,
        tool_calls=[tc.model_dump() for tc in tool_calls],
        backend=req.backend,
    )

    return ChatResponse(
        conversation_id=cid,
        message=result_text,
        model=req.model or "",
        usage=usage,
        tool_calls=tool_calls,
    )


async def _stream_response(req: ChatRequest, cid: str, session_id: str | None = None):
    full_text = ""
    event_count = 0
    try:
        import sys
        with open('/tmp/debug_stream.log', 'a') as _df:
            _df.write(f"[STREAM] 开始 conv={cid} session={session_id} backend={req.backend}\n")
        logger.info("[%s] 开始流式请求 conv=%s session=%s", req.backend, cid, session_id)
        async for event in run_cli_stream(
            prompt=req.message,
            backend=req.backend,
            model=req.model,
            max_turns=req.max_turns,
            work_dir=req.work_dir,
            session_id=session_id,
        ):
            yield event
            event_count += 1
            if event.startswith("data: ") and event.strip() != "data: [DONE]":
                try:
                    ev = json.loads(event[6:])
                    ev_type = ev.get("event", "")
                    if ev_type == "session":
                        sid = ev.get("session_id")
                        if sid:
                            store.set_session_id(cid, req.backend, sid)
                    elif ev_type == "text":
                        full_text += ev.get("text", "")
                    elif ev_type == "result":
                        result_text = ev.get("text", "")
                        if result_text and len(result_text) > len(full_text):
                            full_text = result_text
                    elif ev_type == "error":
                        logger.warning("[%s] 流式错误: %s", req.backend, ev.get("text", ""))
                except json.JSONDecodeError:
                    pass
        logger.info("[%s] 流式请求完成 conv=%s events=%s full_text_len=%s", req.backend, cid, event_count, len(full_text))
    except Exception as e:
        logger.exception("[%s] 流式请求异常 conv=%s", req.backend, cid)
        yield f"data: {json.dumps({'event': 'error', 'text': str(e)})}\n\n"

    if full_text:
        store.append_message(cid, "assistant", full_text)
    elif event_count <= 1:
        logger.warning("[%s] 流式请求无有效事件 conv=%s events=%s", req.backend, cid, event_count)
