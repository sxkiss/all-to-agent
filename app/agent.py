"""Agent 核心：多后端 CLI 调度 + 会话续接 + 事件解析。

上下文过长自动恢复：
- 检测 CLI 输出中的 context/token limit 错误
- 自动截断 session 文件中的旧消息（保留最近的对话）
- 重试时使用截断后的 session，保持浏览器等工具状态不丢失
"""

from __future__ import annotations
import json
import asyncio
import logging
import os
from pathlib import Path
from typing import AsyncGenerator

from app.config import settings
from app.models import Usage

logger = logging.getLogger("agent")

# ── 上下文过长错误检测 ────────────────────────────────

# Claude CLI / API 常见的上下文过长错误关键词
_CONTEXT_ERROR_KEYWORDS = [
    "context_length_exceeded",
    "maximum context length",
    "context window",
    "token limit",
    "too many tokens",
    "request too large",
    "context_length",
    "max_tokens",
    "prompt is too long",
    "conversation is too long",
    "input is too long",
    "overflow",
    "length_after_truncation",
    "PromptTooLongError",
    "ContextWindowExceededError",
]

# 截断时保留的最近对话轮数
KEEP_RECENT_TURNS = 10

def _is_context_too_long_error(stderr_lines: list[str], exit_code: int) -> bool:
    """检测是否为上下文过长错误"""
    if exit_code == 0:
        return False
    for line in stderr_lines:
        line_lower = line.lower()
        for kw in _CONTEXT_ERROR_KEYWORDS:
            if kw in line_lower:
                return True
    return False


def _is_context_too_long_result(result_text: str) -> bool:
    """检测结果文本中是否包含上下文过长错误"""
    if not result_text:
        return False
    text_lower = result_text.lower()
    for kw in _CONTEXT_ERROR_KEYWORDS:
        if kw in text_lower:
            return True
    return False


# ── Session 文件截断 ──────────────────────────────────

def _find_session_file(session_id: str) -> Path | None:
    """查找 Claude CLI 的 session 文件"""
    # Claude CLI 存储路径: ~/.claude/projects/<project>/<session_id>.jsonl
    claude_dir = Path.home() / ".claude" / "projects"
    if not claude_dir.exists():
        return None
    # 搜索所有 project 目录
    for project_dir in claude_dir.iterdir():
        if not project_dir.is_dir():
            continue
        session_file = project_dir / f"{session_id}.jsonl"
        if session_file.exists():
            return session_file
    return None


def _truncate_session_file(session_id: str, keep_turns: int = 10) -> bool:
    """截断 session 文件，保留最近的对话轮次。

    策略：
    1. 读取 JSONL 文件的所有行
    2. 找到 user 类型的消息（代表一轮对话开始）
    3. 保留最近 keep_turns 轮对话的所有相关行
    4. 保留 system/init 消息（session 元数据）
    5. 写回截断后的文件

    Returns: True 如果截断成功
    """
    session_file = _find_session_file(session_id)
    if not session_file or not session_file.exists():
        logger.warning("[session-truncate] 未找到 session 文件: %s", session_id)
        return False

    try:
        # 读取所有行
        lines = session_file.read_text(encoding="utf-8").splitlines()
        if not lines:
            return False

        # 解析所有行
        entries = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue

        if not entries:
            return False

        # 统计文件大小
        original_size = session_file.stat().st_size

        # 找到所有 user 消息的索引（代表一轮对话的开始）
        user_indices = []
        for i, entry in enumerate(entries):
            if entry.get("type") == "user":
                user_indices.append(i)

        # 如果用户消息数不超过 keep_turns，不需要截断
        if len(user_indices) <= keep_turns:
            logger.info("[session-truncate] session %s 消息数 %d <= %d，无需截断",
                       session_id[:12], len(user_indices), keep_turns)
            return False

        # 计算保留范围：保留最近 keep_turns 轮 + 所有 system/init 消息
        cut_index = user_indices[-keep_turns]  # 保留从此索引开始的所有消息

        # 保留 system/init 类型的消息（session 元数据）
        kept_entries = []
        truncated_count = 0
        for i, entry in enumerate(entries):
            entry_type = entry.get("type", "")
            # 保留所有非 user/assistant 的系统消息
            if entry_type not in ("user", "assistant"):
                kept_entries.append(entry)
            # 保留 cut_index 之后的所有消息（最近的对话）
            elif i >= cut_index:
                kept_entries.append(entry)
            else:
                truncated_count += 1

        if truncated_count == 0:
            return False

        # 写回截断后的文件
        new_content = "\n".join(json.dumps(e, ensure_ascii=False) for e in kept_entries) + "\n"
        session_file.write_text(new_content, encoding="utf-8")

        new_size = session_file.stat().st_size
        logger.warning(
            "[session-truncate] session %s 截断完成 | 原始: %dKB -> %dKB | 删除 %d 条旧消息 | 保留 %d 条",
            session_id[:12], original_size // 1024, new_size // 1024,
            truncated_count, len(kept_entries)
        )
        return True

    except Exception as e:
        logger.error("[session-truncate] 截断 session 文件失败: %s | error: %s", session_id, e)
        return False


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
            cmd = ["codex", "exec", "resume", "--json", "--skip-git-repo-check", session_id, prompt]
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
    claude_cmd = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose"]
    if max_turns and max_turns > 0:
        claude_cmd += ["--max-turns", str(max_turns)]
    if session_id:
        claude_cmd += ["--resume", session_id]
    if model:
        claude_cmd += ["--model", model]
    # 用 script 包装，提供伪 TTY（Claude CLI 在无 TTY 时可能行为异常）
    cmd = ["script", "-qec", " ".join(claude_cmd), "/dev/null"]
    return cmd


# ── 事件解析 ──────────────────────────────────────────

def _parse_claude(event: dict, tool_calls: list) -> dict:
    etype = event.get("type", "")
    out = {"session_id": None, "text": "", "final": None, "usage": None, "error": ""}

    if etype == "system" and event.get("subtype") == "init":
        out["session_id"] = event.get("session_id")

    elif etype == "text":
        out["text"] = event.get("text", "")

    elif etype == "assistant":
        for block in event.get("message", {}).get("content", []):
            if block.get("type") == "tool_use":
                tool_calls.append({"type": "call", "name": block.get("name", ""), "input": block.get("input", {})})
            elif block.get("type") == "text":
                # assistant 事件中的文本也需要提取
                text = block.get("text", "")
                if text:
                    out["text"] = text

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
        # 检测 is_error 标志（CLI 执行失败时 result 为空）
        if event.get("is_error", False):
            error_detail = event.get("error", "") or event.get("result", "") or "CLI 执行失败（退出码非0）"
            out["error"] = str(error_detail)
            if not out["final"]:
                out["final"] = f"⚠️ 执行出错: {error_detail}"
        raw = event.get("usage", {})
        if raw:
            out["usage"] = Usage(input_tokens=raw.get("input_tokens", 0), output_tokens=raw.get("output_tokens", 0))

    elif etype == "error":
        # 捕获 stdout 中的错误事件（Claude CLI 上下文过长等错误在这里输出）
        error_text = event.get("error", "") or event.get("message", "") or event.get("text", "")
        if error_text:
            out["error"] = str(error_text)

    return out


def _parse_codex(event: dict, tool_calls: list) -> dict:
    out = {"session_id": None, "text": "", "final": None, "usage": None, "error": ""}

    if event.get("type") == "thread.started":
        out["session_id"] = event.get("thread_id")

    elif event.get("type") == "item.completed":
        item = event.get("item", {})
        itype = item.get("type", "")
        if itype == "agent_message":
            out["text"] = item.get("text", "")
        elif itype == "tool_call":
            tool_calls.append({"type": "call", "name": item.get("name", ""), "input": item.get("arguments", {})})
        elif itype == "command_execution":
            # codex CLI 的工具调用类型是 command_execution
            tc = {"type": "call", "name": item.get("command", "shell"), "input": {"command": item.get("command", "")}}
            tc["result"] = item.get("aggregated_output", "")
            tool_calls.append(tc)
        elif itype == "mcp_tool_call":
            # codex resume 的 MCP 工具调用
            tool_calls.append({
                "type": "call",
                "name": item.get("tool", ""),
                "input": item.get("arguments", {}),
                "result": item.get("result", ""),
            })
        elif itype == "tool_result" and tool_calls:
            tool_calls[-1]["result"] = item.get("output", "")

    elif event.get("type") == "turn.completed":
        raw = event.get("usage", {})
        if raw:
            out["usage"] = Usage(input_tokens=raw.get("input_tokens", 0), output_tokens=raw.get("output_tokens", 0))

    elif event.get("type") in ("error", "turn.failed"):
        error_text = event.get("error", "") or event.get("message", "") or event.get("text", "")
        if error_text:
            out["error"] = str(error_text)

    return out


def _parse_opencode(event: dict, tool_calls: list) -> dict:
    etype = event.get("type", "")
    out = {"session_id": None, "text": "", "final": None, "usage": None, "error": ""}

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

    elif etype in ("error", "step_error"):
        error_text = event.get("error", "") or event.get("message", "") or event.get("text", "")
        if error_text:
            out["error"] = str(error_text)

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
    """调用 CLI，支持 session_id 续接。返回 {result, tool_calls, usage, session_id}。

    上下文过长自动恢复（保留 session 状态）：
    1. 带 session_id 调用失败时，检测是否为上下文过长错误
    2. 如果是，截断 session 文件中的旧消息（保留最近10轮）
    3. 使用截断后的 session 重试（保持 session_id 和浏览器等工具状态）
    """
    result = await _run_cli_inner(
        prompt=prompt, backend=backend, model=model,
        max_turns=max_turns, work_dir=work_dir, session_id=session_id,
    )

    # 检测上下文过长错误并截断重试（带 session 时才需要）
    if session_id and backend == "claude":
        stderr = result.get("_stderr_lines", [])
        stdout_errors = result.get("_stdout_errors", [])
        exit_code = result.get("_cli_exit_code", 0)
        result_text = result.get("result", "")

        is_context_error = (
            _is_context_too_long_error(stderr, exit_code)
            or _is_context_too_long_result(result_text)
            or any(_is_context_too_long_result(err) for err in stdout_errors)
        )

        if is_context_error:
            logger.warning(
                "[%s] 上下文过长，截断 session 旧消息重试 | session=%s | exit_code=%d | stdout_errors=%s",
                backend, session_id[:12], exit_code, stdout_errors[:2]
            )
            # 截断 session 文件（保留最近10轮对话）
            truncated = _truncate_session_file(session_id, keep_turns=KEEP_RECENT_TURNS)
            if truncated:
                # 用截断后的 session 重试
                result = await _run_cli_inner(
                    prompt=prompt, backend=backend, model=model,
                    max_turns=max_turns, work_dir=work_dir, session_id=session_id,
                )
                logger.info("[agent] 截断重试完成 | new_session=%s | result_len=%d",
                           result.get("session_id", "N/A")[:12],
                           len(result.get("result", "")))
            else:
                logger.error("[agent] session 截断失败，无法重试")

    # 清理内部字段
    result.pop("_cli_exit_code", None)
    result.pop("_stderr_lines", None)
    result.pop("_stdout_errors", None)
    return result


async def _run_cli_inner(
    prompt: str,
    backend: str = "claude",
    model: str | None = None,
    max_turns: int = 10,
    work_dir: str | None = None,
    session_id: str | None = None,
) -> dict:
    """内部 CLI 调用实现"""
    cmd = _build_cmd(backend, prompt, model, max_turns, work_dir, session_id)
    logger.info("[%s] %s", backend, " ".join(cmd[:8]))

    # limit 增大到 50MB，避免 CLI 输出超长行（如浏览器快照、长会话）导致 LimitOverrunError
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        cwd=work_dir or settings.WORK_DIR,
        limit=50 * 1024 * 1024,
    )

    parser = PARSERS.get(backend, _parse_claude)
    text_parts = []
    tool_calls = []
    result_usage = None
    new_session_id = None
    stderr_lines = []
    stdout_errors = []  # 收集 stdout 中的错误事件

    async def read_stderr():
        async for line in proc.stderr:
            decoded = line.decode().strip()
            if decoded:
                stderr_lines.append(decoded)
                logger.debug("[%s] stderr: %s", backend, decoded)

    stderr_task = asyncio.create_task(read_stderr())

    # 按块读取 stdout，避免 readline() 的行长度限制
    buffer = ""
    while True:
        chunk = await proc.stdout.read(64 * 1024)  # 每次读 64KB
        if not chunk:
            break  # EOF
        buffer += chunk.decode("utf-8", errors="replace")
        # 按换行分割，保留最后可能不完整的行
        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            raw = line.strip()
            if not raw:
                continue
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("[%s] 无法解析为 JSON 的行: %s", backend, raw[:200])
                text_parts.append(raw)
                continue
            parsed = parser(event, tool_calls)
            if parsed["session_id"] and not new_session_id:
                new_session_id = parsed["session_id"]
            if parsed["text"]:
                text_parts.append(parsed["text"])
            if parsed["usage"]:
                result_usage = parsed["usage"]
            if parsed.get("error"):
                stdout_errors.append(parsed["error"])
            # final 只在比已有内容更长时才替换（避免截断）
            if parsed["final"] and len(parsed["final"]) > len("".join(text_parts)):
                text_parts = [parsed["final"]]

    # 处理 buffer 中剩余的最后一行
    if buffer.strip():
        raw = buffer.strip()
        try:
            event = json.loads(raw)
            parsed = parser(event, tool_calls)
            if parsed["session_id"] and not new_session_id:
                new_session_id = parsed["session_id"]
            if parsed["text"]:
                text_parts.append(parsed["text"])
            if parsed["usage"]:
                result_usage = parsed["usage"]
            if parsed["final"] and len(parsed["final"]) > len("".join(text_parts)):
                text_parts = [parsed["final"]]
        except json.JSONDecodeError:
            text_parts.append(raw)

    await stderr_task
    await proc.wait()

    # CLI 退出码非0时记录警告，但不抛异常（退出码1常见于 MCP/工具调用问题，不影响已输出的内容）
    if proc.returncode != 0:
        stderr_msg = "\n".join(stderr_lines[-3:]) if stderr_lines else "(无 stderr)"
        logger.warning("[%s] CLI 退出码 %d: %s", backend, proc.returncode, stderr_msg)

    # 如果文本为空但有工具调用，从工具结果中提取有意义的内容
    final_text = "".join(text_parts).strip()
    if not final_text and tool_calls:
        # 尝试从最后一个工具调用的结果中提取
        for tc in reversed(tool_calls):
            result = tc.get("result", "")
            if result and len(result) > 20:
                final_text = result[:2000]
                break
        if not final_text:
            tool_names = [tc.get("name", "Unknown") for tc in tool_calls[:5]]
            final_text = f"已完成 {len(tool_calls)} 个工具调用：{', '.join(tool_names)}"
            if len(tool_calls) > 5:
                final_text += f" 等 {len(tool_calls)} 个"
        logger.info("[%s] 无文本输出，生成摘要: %s", backend, final_text[:100])

    return {
        "result": final_text,
        "tool_calls": tool_calls,
        "usage": result_usage,
        "session_id": new_session_id,
        "_cli_exit_code": proc.returncode,
        "_stderr_lines": stderr_lines,
        "_stdout_errors": stdout_errors,
    }


async def run_cli_stream(
    prompt: str,
    backend: str = "claude",
    model: str | None = None,
    max_turns: int = 10,
    work_dir: str | None = None,
    session_id: str | None = None,
) -> AsyncGenerator[str, None]:
    """真正流式：直接读 CLI 进程 stdout，实时 yield SSE 事件。"""
    import time, sys
    start = time.time()
    cwd = work_dir or settings.WORK_DIR
    cmd = _build_cmd(backend, prompt, model, max_turns, work_dir, session_id)
    with open('/tmp/debug_stream.log', 'a') as _df:
        _df.write(f"[STREAM] backend={backend} session={session_id} cmd={cmd[:8]}\n")
        _df.flush()
    logger.info("[%s stream] 启动: %s", backend, " ".join(cmd[:8]))

    proc = await asyncio.create_subprocess_exec(
        *cmd, stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        cwd=cwd, limit=50 * 1024 * 1024,
    )

    with open('/tmp/debug_stream.log', 'a') as _df:
        _df.write(f"[STREAM] 进程已启动 pid={proc.pid}\n")
        _df.flush()
    logger.info("[%s stream] 进程已启动 pid=%s returncode=%s", backend, proc.pid, proc.returncode)
    parser = PARSERS.get(backend, _parse_claude)
    tool_calls = []
    full_text = ""
    session_id_out = None
    result_usage = None

    # 读 stderr（非阻塞）
    stderr_lines = []
    async def _read_stderr():
        async for line in proc.stderr:
            d = line.decode().strip()
            if d:
                stderr_lines.append(d)
    stderr_task = asyncio.create_task(_read_stderr())

    # 直接从 stdout 逐块读取，实时 yield
    with open('/tmp/debug_stream.log', 'a') as _df:
        _df.write(f"[STREAM] 进入 stdout 循环\n")
        _df.flush()
    buffer = ""
    event_count = 0
    try:
        while True:
            chunk = await proc.stdout.read(64 * 1024)
            if not chunk:
                logger.info("[%s stream] stdout EOF (CLI 关闭输出) | buffer_len=%d | events=%d", backend, len(buffer), event_count)
                break
            logger.info("[%s stream] 收到 %d 字节", backend, len(chunk))
            buffer += chunk.decode("utf-8", errors="replace")
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                raw = line.strip()
                if not raw:
                    continue
                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                parsed = parser(event, tool_calls)
                event_count += 1
                etype = event.get("type", "")

                # 每10个事件记录一次进度
                if event_count % 10 == 0:
                    logger.info("[%s stream] 进度 | events=%d | text=%d | tools=%d | elapsed=%.0fs",
                               backend, event_count, len(full_text), len(tool_calls), time.time() - start)

                # session → yield 立即
                if parsed["session_id"]:
                    session_id_out = parsed["session_id"]
                    yield f"data: {json.dumps({'event': 'session', 'session_id': session_id_out})}\n\n"

                # tool_call → yield 立即
                if tool_calls and tool_calls[-1].get("name") and not tool_calls[-1].get("_yielded"):
                    tc = tool_calls[-1]
                    tc["_yielded"] = True
                    yield f"data: {json.dumps({'event': 'tool_call', 'name': tc.get('name', ''), 'input': tc.get('input', {})})}\n\n"

                # tool_result → yield 立即
                if tool_calls and tool_calls[-1].get("result") and not tool_calls[-1].get("_result_yielded"):
                    tc = tool_calls[-1]
                    tc["_result_yielded"] = True
                    yield f"data: {json.dumps({'event': 'tool_result', 'content': str(tc['result'])[:500]})}\n\n"

                # text → yield 立即（逐段发给用户）
                if parsed["text"]:
                    full_text += parsed["text"]
                    yield f"data: {json.dumps({'event': 'text', 'text': parsed['text']})}\n\n"

                # error → yield 立即
                if parsed.get("error"):
                    yield f"data: {json.dumps({'event': 'error', 'text': parsed['error']})}\n\n"

                # result → 覆盖 full_text，捕获 usage
                if parsed["final"] and len(parsed["final"]) > len(full_text):
                    full_text = parsed["final"]
                    logger.info("[%s stream] 收到 result | is_error=%s | result_len=%d",
                               backend, event.get("is_error", False), len(parsed["final"]))
                if parsed["usage"]:
                    result_usage = parsed["usage"]

    except Exception as e:
        logger.error("[%s stream] 读取异常: %s", backend, e, exc_info=True)
        yield f"data: {json.dumps({'event': 'error', 'text': str(e)})}\n\n"

    # 处理 buffer 中剩余内容
    if buffer.strip():
        try:
            event = json.loads(buffer.strip())
            parsed = parser(event, tool_calls)
            if parsed["session_id"]:
                session_id_out = parsed["session_id"]
                yield f"data: {json.dumps({'event': 'session', 'session_id': session_id_out})}\n\n"
            if parsed["text"]:
                full_text += parsed["text"]
                yield f"data: {json.dumps({'event': 'text', 'text': parsed['text']})}\n\n"
            if parsed["final"] and len(parsed["final"]) > len(full_text):
                full_text = parsed["final"]
        except json.JSONDecodeError:
            pass

    await stderr_task
    await proc.wait()
    elapsed = time.time() - start

    # CLI 退出码非0
    if proc.returncode != 0:
        stderr_msg = "\n".join(stderr_lines[-3:]) if stderr_lines else "(无 stderr)"
        logger.warning("[%s stream] CLI 退出码 %d: %s", backend, proc.returncode, stderr_msg)

    # yield result 事件（完整回复）
    usage_dict = None
    if result_usage:
        usage_dict = result_usage.model_dump() if hasattr(result_usage, 'model_dump') else result_usage
    yield f"data: {json.dumps({'event': 'result', 'text': full_text, 'session_id': session_id_out, 'conversation_id': None, 'usage': usage_dict})}\n\n"
    yield "data: [DONE]\n\n"

    with open('/tmp/debug_stream.log', 'a') as _df:
        _df.write(f"[STREAM] yield result | text={len(full_text)} tools={len(tool_calls)} exit={proc.returncode}\n")
        _df.flush()
    logger.info("[%s stream] 完成 | %.1fs | text_len=%d | tools=%d | exit=%d",
                backend, elapsed, len(full_text), len(tool_calls), proc.returncode or 0)
