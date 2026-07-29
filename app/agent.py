"""Agent 核心：通过 subprocess 调用本机 claude CLI，解析所有事件。"""

from __future__ import annotations
import json
import asyncio
import logging
from typing import AsyncGenerator

from app.config import settings
from app.models import Usage

logger = logging.getLogger("agent")


def _build_cmd(
    prompt: str,
    model: str | None = None,
    max_turns: int = 10,
    work_dir: str | None = None,
    system_prompt: str | None = None,
    output_format: str = "stream-json",
) -> list[str]:
    cmd = [
        settings.CLAUDE_BIN,
        "-p", prompt,
        "--output-format", output_format,
        "--max-turns", str(max_turns),
        "--verbose",
    ]
    if model:
        cmd += ["--model", model]
    if system_prompt:
        cmd += ["--system-prompt", system_prompt]
    return cmd


async def run_cli_collect(
    prompt: str,
    model: str | None = None,
    max_turns: int = 10,
    work_dir: str | None = None,
    system_prompt: str | None = None,
) -> dict:
    """用 stream-json 格式调用 claude CLI，收集所有事件。返回 {result, tool_calls, model, usage}。"""
    cmd = _build_cmd(prompt, model, max_turns, work_dir, system_prompt)
    logger.info("CLI: %s", " ".join(cmd[:6]))

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=work_dir or settings.WORK_DIR,
    )

    text_parts = []
    tool_calls = []
    result_model = ""
    result_usage = None

    async for line in proc.stdout:
        text = line.decode().strip()
        if not text:
            continue
        try:
            event = json.loads(text)
        except json.JSONDecodeError:
            continue

        etype = event.get("type", "")

        # 流式文本片段
        if etype == "text":
            delta = event.get("text", "")
            if delta:
                text_parts.append(delta)

        # 工具调用 — 嵌套在 assistant 消息中
        elif etype == "assistant":
            content = event.get("message", {}).get("content", [])
            for block in content:
                btype = block.get("type", "")
                if btype == "tool_use":
                    tool_calls.append({
                        "type": "call",
                        "name": block.get("name", ""),
                        "input": block.get("input", {}),
                    })

        # 工具结果
        elif etype == "user":
            content = event.get("message", {}).get("content", [])
            for block in content:
                if block.get("type") == "tool_result":
                    if tool_calls:
                        tc_result = block.get("content", "")
                        if isinstance(tc_result, list):
                            tc_result = " ".join(
                                item.get("text", "") if isinstance(item, dict) else str(item)
                                for item in tc_result
                            )
                        tool_calls[-1]["result"] = tc_result

        # 最终结果
        elif etype == "result":
            result_model = event.get("modelUsage", {}).get("claude-opus-4-8[1m]", {}).get("model", "")
            raw_usage = event.get("usage", {})
            if raw_usage:
                result_usage = Usage(
                    input_tokens=raw_usage.get("input_tokens", 0),
                    output_tokens=raw_usage.get("output_tokens", 0),
                )
            final_text = event.get("result", "")
            if final_text:
                text_parts = [final_text]

    await proc.wait()

    if proc.returncode != 0:
        stderr = (await proc.stderr.read()).decode().strip()
        raise RuntimeError(f"CLI 错误 (code {proc.returncode}): {stderr}")

    return {
        "result": "".join(text_parts),
        "tool_calls": tool_calls,
        "model": result_model,
        "usage": result_usage,
    }


async def run_cli_stream(
    prompt: str,
    model: str | None = None,
    max_turns: int = 10,
    work_dir: str | None = None,
    system_prompt: str | None = None,
) -> AsyncGenerator[str, None]:
    """流式：逐行 yield SSE 事件，包含工具调用。"""
    cmd = _build_cmd(prompt, model, max_turns, work_dir, system_prompt)

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=work_dir or settings.WORK_DIR,
    )

    async for line in proc.stdout:
        text = line.decode().strip()
        if text:
            yield f"data: {text}\n\n"

    yield "data: [DONE]\n\n"
