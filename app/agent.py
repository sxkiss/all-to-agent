"""Agent 核心：统一调用 Claude / Codex / OpenCode CLI，解析事件。"""

from __future__ import annotations
import json
import asyncio
import logging
from typing import AsyncGenerator

from app.config import settings
from app.models import Usage

logger = logging.getLogger("agent")


# ── CLI 配置 ──────────────────────────────────────────

BACKENDS = {
    "claude": {
        "bin": "claude",
        "base_args": lambda prompt, mt: ["-p", prompt, "--output-format", "stream-json", "--verbose", "--max-turns", str(mt)],
        "model_arg": lambda m: ["--model", m] if m else [],
    },
    "codex": {
        "bin": "codex",
        "base_args": lambda prompt, mt: ["exec", "--json", "--skip-git-repo-check", prompt],
        "model_arg": lambda m: ["-c", f'model="{m}"'] if m else [],
    },
    "opencode": {
        "bin": "opencode",
        "base_args": lambda prompt, mt: ["run", "--format", "json", "--dir", settings.WORK_DIR, prompt],
        "model_arg": lambda m: ["-m", m] if m else [],
    },
}


def _build_cmd(
    backend: str,
    prompt: str,
    model: str | None = None,
    max_turns: int = 10,
    work_dir: str | None = None,
) -> list[str]:
    cfg = BACKENDS.get(backend, BACKENDS["claude"])
    cmd = [cfg["bin"]] + cfg["base_args"](prompt, max_turns) + cfg["model_arg"](model)
    logger.info("[%s] %s", backend, " ".join(cmd[:8]))
    return cmd


# ── 事件解析 ──────────────────────────────────────────

def _parse_event_claude(event: dict, tool_calls: list) -> dict:
    """解析 Claude stream-json 事件。"""
    etype = event.get("type", "")
    result = {"text": "", "tool": None, "final": None}

    if etype == "text":
        result["text"] = event.get("text", "")

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
        result["final"] = event.get("result", "")
        raw_usage = event.get("usage", {})
        usage = None
        if raw_usage:
            usage = Usage(input_tokens=raw_usage.get("input_tokens", 0), output_tokens=raw_usage.get("output_tokens", 0))
        result["usage"] = usage

    return result


def _parse_event_codex(event: dict, tool_calls: list) -> dict:
    """解析 Codex JSONL 事件。"""
    result = {"text": "", "tool": None, "final": None}

    if event.get("type") == "item.completed":
        item = event.get("item", {})
        item_type = item.get("type", "")

        if item_type == "agent_message":
            result["text"] = item.get("text", "")

        elif item_type == "tool_call":
            tool_calls.append({
                "type": "call",
                "name": item.get("name", ""),
                "input": item.get("arguments", {}),
            })

        elif item_type == "tool_result":
            if tool_calls:
                tool_calls[-1]["result"] = item.get("output", "")

    elif event.get("type") == "turn.completed":
        usage_raw = event.get("usage", {})
        usage = Usage(
            input_tokens=usage_raw.get("input_tokens", 0),
            output_tokens=usage_raw.get("output_tokens", 0),
        ) if usage_raw else None
        result["usage"] = usage

    return result


def _parse_event_opencode(event: dict, tool_calls: list) -> dict:
    """解析 OpenCode JSON 事件。"""
    etype = event.get("type", "")
    result = {"text": "", "tool": None, "final": None}

    if etype == "text":
        result["text"] = event.get("part", {}).get("text", "")

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
        part = event.get("part", {})
        tokens = part.get("tokens", {})
        usage = Usage(
            input_tokens=tokens.get("input", 0) if tokens else 0,
            output_tokens=tokens.get("output", 0) if tokens else 0,
        ) if tokens else None
        result["usage"] = usage

    return result


PARSERS = {
    "claude": _parse_event_claude,
    "codex": _parse_event_codex,
    "opencode": _parse_event_opencode,
}


# ── 公开接口 ──────────────────────────────────────────

async def run_cli_collect(
    prompt: str,
    backend: str = "claude",
    model: str | None = None,
    max_turns: int = 10,
    work_dir: str | None = None,
) -> dict:
    """调用 CLI，收集事件返回 {result, tool_calls, model, usage}。"""
    cmd = _build_cmd(backend, prompt, model, max_turns, work_dir)
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        cwd=work_dir or settings.WORK_DIR,
    )

    parser = PARSERS.get(backend, _parse_event_claude)
    text_parts = []
    tool_calls = []
    result_usage = None

    async for line in proc.stdout:
        text = line.decode().strip()
        if not text:
            continue
        try:
            event = json.loads(text)
        except json.JSONDecodeError:
            continue
        parsed = parser(event, tool_calls)
        if parsed["text"]:
            text_parts.append(parsed["text"])
        if "usage" in parsed and parsed["usage"]:
            result_usage = parsed["usage"]
        if parsed["final"]:
            text_parts = [parsed["final"]]

    await proc.wait()
    if proc.returncode != 0:
        stderr = (await proc.stderr.read()).decode().strip()
        raise RuntimeError(f"CLI 错误 (code {proc.returncode}): {stderr}")

    return {"result": "".join(text_parts), "tool_calls": tool_calls, "usage": result_usage}


async def run_cli_stream(
    prompt: str,
    backend: str = "claude",
    model: str | None = None,
    max_turns: int = 10,
    work_dir: str | None = None,
) -> AsyncGenerator[str, None]:
    """流式：yield SSE 事件。"""
    cmd = _build_cmd(backend, prompt, model, max_turns, work_dir)
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        cwd=work_dir or settings.WORK_DIR,
    )
    async for line in proc.stdout:
        text = line.decode().strip()
        if text:
            yield f"data: {text}\n\n"
    yield "data: [DONE]\n\n"
