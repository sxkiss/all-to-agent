"""Pydantic 数据模型：请求、响应、存储。"""

from __future__ import annotations
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ── 请求 ──────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None
    stream: bool = False
    system_prompt: str | None = None
    model: str | None = None
    max_turns: int = 0  # 0=不限制，让CLI自行决定何时完成
    work_dir: str | None = None
    backend: str = "claude"  # claude | codex | opencode
    reset_session: bool = False  # 重置会话，新开一个 CLI session


# ── 响应 ──────────────────────────────────────────────

class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0


class ToolCall(BaseModel):
    type: str = "call"  # call | result
    name: str = ""
    input: dict = {}
    result: str = ""


class ChatResponse(BaseModel):
    conversation_id: str
    message: str
    model: str = ""
    usage: Usage | None = None
    tool_calls: list[ToolCall] = []
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())


class ConversationSummary(BaseModel):
    id: str
    title: str
    message_count: int
    created_at: str
    updated_at: str


# ── 定时任务 ──────────────────────────────────────────

class TaskType(str, Enum):
    ONCE = "once"
    CRON = "cron"
    INTERVAL = "interval"


class TaskCreate(BaseModel):
    name: str
    type: TaskType
    schedule: str  # ISO 时间 / cron 表达式 / "5m"
    prompt: str
    enabled: bool = True


class Task(BaseModel):
    id: str
    name: str
    type: TaskType
    schedule: str
    prompt: str
    enabled: bool = True
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    last_run: str | None = None


# ── 存储内部模型 ──────────────────────────────────────

class Message(BaseModel):
    role: str  # "user" | "assistant"
    content: str
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())
    model: str = ""
    usage: Usage | None = None


class Conversation(BaseModel):
    id: str
    title: str = ""
    messages: list[Message] = []
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now().isoformat())


# ── 通用 ──────────────────────────────────────────────

class ErrorResponse(BaseModel):
    error: str
    detail: str = ""


class HealthResponse(BaseModel):
    status: str = "ok"
    assistant: str
    uptime_seconds: float
    active_tasks: int = 0
