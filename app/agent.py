"""Agent 核心：多后端 CLI 调度 + 会话续接 + 事件解析。"""

from __future__ import annotations
import json
import asyncio
import logging
from typing import AsyncGenerator

from app.config import settings
from app.models import Usage

logger = logging.getLogger("agent")


# ── 后端配置 ──────────────────────────────────────────

def _build_cmd(
    backend: str,
    prompt: str,
    model: str | None = None,
    max_turns: int = 10,
    work_dir: str | None = None,
    session_id: str | None = None,
) -> list[str]:
    """构建各后端 CLI 命令，支持 session_id 续接。"""
    cwd = work_dir or settings.WORK_DIR

    if backend == "codex":
        if session_id:
            cmd = ["codex", "resume", "--json", session_id, prompt]
        else:
            cmd = ["codex", "exec", "--json", "--skip-git-repo-check", prompt]
        if model:
            cmd += ["-c", f'model="{model}"']
        return cmd

    if backend == "opencode":
        cmd = ["opencode", "run", "--format", "json", "--dir", cwd]
        if session_id:
            cmd += ["-s", session_id]
        cmd.append(prompt)
        if model:
            cmd += ["-m", model]
        return cmd

    # claude (默认)
    cmd = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose", "--max-turns", str(max_turns)]
    if session_id:
        cmd += ["--resume", session_id]
    if model:
        cmd += ["--model", model]
    return cmd


# ── 事件解析 ──────────────────────────────────────────

def _parse_claude(event: dict, tool_calls: list) -> dict:
    etype = event.get("type", "")
    out = {"session_id": None, "text": "", "final": None, "usage": None}

    if etype == "system" and event.get("subtype") == "init":
        out["session_id"] = event.get("session_id")

    elif etype == "text":
        out["text"] = event.get("text", "")

    elif etype == "assistant":
        for block in event.get("message", {}).get("content", []):
            if block.get("type") == "tool_use":
                tool_calls.append({"type": "call", "name": block.get("name", ""), "input": block.get("input", {})})

    elif etype == "user":
        for block in event.get("message", {}).get("content", []):
            if block.get("type") == "tool_result" and tool_calls:
                tc_result = block.get("content", "")
                if isinstance(tc_result, list):
                    tc_result = " ".join(item.get("text", "") if isinstance(item, dict) else str(item) for item in tc_result)
                tool_calls[-1]["result"] = tc_result

    elif etype == "result":
        out["final"] = event.get("result", "")
        out["session_id"] = event.get("session_id")
        raw = event.get("usage", {})
        if raw:
            out["usage"] = Usage(input_tokens=raw.get("input_tokens", 0), output_tokens=raw.get("output_tokens", 0))

    return out


def _parse_codex(event: dict, tool_calls: list) -> dict:
    out = {"session_id": None, "text": "", "final": None, "usage": None}

    if event.get("type") == "thread.started":
        out["session_id"] = event.get("thread_id")

    elif event.get("type") == "item.completed":
        item = event.get("item", {})
        itype = item.get("type", "")
        if itype == "agent_message":
            out["text"] = item.get("text", "")
        elif itype == "tool_call":
            tool_calls.append({"type": "call", "name": item.get("name", ""), "input": item.get("arguments", {})})
        elif itype == "tool_result" and tool_calls:
            tool_calls[-1]["result"] = item.get("output", "")

    elif event.get("type") == "turn.completed":
        raw = event.get("usage", {})
        if raw:
            out["usage"] = Usage(input_tokens=raw.get("input_tokens", 0), output_tokens=raw.get("output_tokens", 0))

    return out


def _parse_opencode(event: dict, tool_calls: list) -> dict:
    etype = event.get("type", "")
    out = {"session_id": None, "text": "", "final": None, "usage": None}

    if etype == "step_start":
        out["session_id"] = event.get("sessionID")

    elif etype == "text":
        out["text"] = event.get("part", {}).get("text", "")

    elif etype == "tool_use":
        part = event.get("part", {})
        state = part.get("state", {})
        tool_calls.append({
            "type": "call",
            "name": part.get("tool", ""),
            "input": state.get("input", {}),
            "result": state.get("output", ""),
        })

    elif etype == "step_finish":
        out["session_id"] = event.get("sessionID")
        raw = event.get("part", {}).get("tokens", {})
        if raw:
            out["usage"] = Usage(input_tokens=raw.get("input", 0), output_tokens=raw.get("output", 0))

    return out


PARSERS = {"claude": _parse_claude, "codex": _parse_codex, "opencode": _parse_opencode}


# ── 公开接口 ──────────────────────────────────────────

async def run_cli_collect(
    prompt: str,
    backend: str = "claude",
    model: str | None = None,
    max_turns: int = 10,
    work_dir: str | None = None,
    session_id: str | None = None,
) -> dict:
    """调用 CLI，支持 session_id 续接。返回 {result, tool_calls, usage, session_id}。"""
    cmd = _build_cmd(backend, prompt, model, max_turns, work_dir, session_id)
    logger.info("[%s] %s", backend, " ".join(cmd[:8]))

    proc = await asyncio.create_subprocess_exec(
        *cmd, stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        cwd=work_dir or settings.WORK_DIR,
    )

    parser = PARSERS.get(backend, _parse_claude)
    text_parts = []
    tool_calls = []
    result_usage = None
    new_session_id = None
    stderr_lines = []

    async def read_stderr():
        async for line in proc.stderr:
            decoded = line.decode().strip()
            if decoded:
                stderr_lines.append(decoded)
                logger.debug("[%s] stderr: %s", backend, decoded)

    stderr_task = asyncio.create_task(read_stderr())

    async for line in proc.stdout:
        raw = line.decode().strip()
        if not raw:
            continue
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("[%s] 无法解析为 JSON 的行: %s", backend, raw[:200])
            text_parts.append(raw)  # 将非 JSON 文本也作为结果收集，避免静默丢失
            continue
        parsed = parser(event, tool_calls)
        if parsed["session_id"] and not new_session_id:
            new_session_id = parsed["session_id"]
        if parsed["text"]:
            text_parts.append(parsed["text"])
        if parsed["usage"]:
            result_usage = parsed["usage"]
        if parsed["final"]:
            text_parts = [parsed["final"]]

    await stderr_task
    await proc.wait()

    if proc.returncode != 0:
        stderr_msg = "\n".join(stderr_lines) if stderr_lines else "(无 stderr 输出)"
        raise RuntimeError(f"CLI 错误 (code {proc.returncode}): {stderr_msg}")

    return {
        "result": "".join(text_parts),
        "tool_calls": tool_calls,
        "usage": result_usage,
        "session_id": new_session_id,
    }


async def run_cli_stream(
    prompt: str,
    backend: str = "claude",
    model: str | None = None,
    max_turns: int = 10,
    work_dir: str | None = None,
    session_id: str | None = None,
) -> AsyncGenerator[str, None]:
    """流式：解析 CLI 事件，yield 结构化 SSE 事件。带心跳防超时。"""
    cmd = _build_cmd(backend, prompt, model, max_turns, work_dir, session_id)
    logger.info("[%s stream] %s", backend, " ".join(cmd[:8]))

    proc = await asyncio.create_subprocess_exec(
        *cmd, stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        cwd=work_dir or settings.WORK_DIR,
    )

    # stderr 异步读取防死锁
    stderr_lines = []

    async def _read_stderr():
        async for line in proc.stderr:
            d = line.decode().strip()
            if d:
                stderr_lines.append(d)
                logger.debug("[%s stderr] %s", backend, d[:200])

    stderr_task = asyncio.create_task(_read_stderr())

    # stdout → Queue，方便在主线程中同时处理心跳
    line_queue: asyncio.Queue[str | None] = asyncio.Queue()

    async def _enqueue_stdout():
        async for raw_line in proc.stdout:
            line_queue.put_nowait(raw_line.decode().strip())
        line_queue.put_nowait(None)  # EOF 信号

    stdout_task = asyncio.create_task(_enqueue_stdout())

    sent_text_len = 0
    heartbeat_interval = 15  # 秒

    while True:
        try:
            raw = await asyncio.wait_for(line_queue.get(), timeout=heartbeat_interval)
        except asyncio.TimeoutError:
            # 无数据 → 发心跳保活
            yield ": heartbeat\n\n"
            continue

        if raw is None:
            break  # stdout EOF

        if not raw:
            continue
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            yield f"data: {json.dumps({'event': 'text', 'text': raw})}\n\n"
            continue

        etype = event.get("type", "")

        # ── Claude 后端 ──
        if backend == "claude":
            if etype == "system" and event.get("subtype") == "init":
                sid = event.get("session_id")
                if sid:
                    yield f"data: {json.dumps({'event': 'session', 'session_id': sid})}\n\n"

            elif etype == "assistant":
                for block in event.get("message", {}).get("content", []):
                    btype = block.get("type")
                    if btype == "tool_use":
                        yield f"data: {json.dumps({'event': 'tool_call', 'name': block.get('name', ''), 'input': block.get('input', {})})}\n\n"
                    elif btype == "text":
                        full_text = block.get("text", "")
                        delta = full_text[sent_text_len:]
                        if delta:
                            yield f"data: {json.dumps({'event': 'text', 'text': delta})}\n\n"
                            sent_text_len = len(full_text)

            elif etype == "user":
                for block in event.get("message", {}).get("content", []):
                    if block.get("type") == "tool_result":
                        yield f"data: {json.dumps({'event': 'tool_result', 'content': str(block.get('content', ''))[:500]})}\n\n"

            elif etype == "result":
                final = event.get("result", "")
                sid = event.get("session_id")
                usage = event.get("usage", {})
                if final and len(final) > sent_text_len:
                    yield f"data: {json.dumps({'event': 'text', 'text': final[sent_text_len:]})}\n\n"
                yield f"data: {json.dumps({'event': 'result', 'text': final, 'session_id': sid, 'usage': usage})}\n\n"

        # ── Codex 后端 ──
        elif backend == "codex":
            if etype == "thread.started":
                sid = event.get("thread_id")
                if sid:
                    yield f"data: {json.dumps({'event': 'session', 'session_id': sid})}\n\n"
            elif etype == "item.completed":
                item = event.get("item", {})
                itype = item.get("type", "")
                if itype == "agent_message":
                    yield f"data: {json.dumps({'event': 'text', 'text': item.get('text', '')})}\n\n"
                elif itype == "tool_call":
                    yield f"data: {json.dumps({'event': 'tool_call', 'name': item.get('name', ''), 'input': item.get('arguments', {})})}\n\n"
                elif itype == "tool_result":
                    yield f"data: {json.dumps({'event': 'tool_result', 'content': str(item.get('output', ''))[:500]})}\n\n"
            elif etype == "turn.completed":
                usage = event.get("usage", {})
                yield f"data: {json.dumps({'event': 'result', 'text': '', 'usage': usage})}\n\n"

        # ── OpenCode 后端 ──
        elif backend == "opencode":
            if etype == "step_start":
                sid = event.get("sessionID")
                if sid:
                    yield f"data: {json.dumps({'event': 'session', 'session_id': sid})}\n\n"
            elif etype == "text":
                part = event.get("part", {})
                delta = part.get("text", "")
                if delta:
                    yield f"data: {json.dumps({'event': 'text', 'text': delta})}\n\n"
            elif etype == "tool_use":
                part = event.get("part", {})
                state = part.get("state", {})
                yield f"data: {json.dumps({'event': 'tool_call', 'name': part.get('tool', ''), 'input': state.get('input', {})})}\n\n"
            elif etype == "step_finish":
                yield f"data: {json.dumps({'event': 'result', 'text': '', 'usage': event.get('part', {}).get('tokens', {})})}\n\n"

    stdout_task.cancel()
    await stderr_task
    await proc.wait()

    if proc.returncode != 0:
        yield f"data: {json.dumps({'event': 'error', 'text': 'CLI 错误 (code ' + str(proc.returncode) + '): ' + chr(10).join(stderr_lines[-3:])})}\n\n"

    yield "data: [DONE]\n\n"
