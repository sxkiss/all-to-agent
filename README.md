# Claude / Codex / OpenCode 个人助理 Agent API

将本机 AI CLI 封装为 HTTP API，统一提供对话、会话续接、工具调用反馈、Web 界面等能力。

**支持后端**：`claude` · `codex` · `opencode`（本机已安装的 CLI，复用其全部工具和配置）

---

## 快速开始

```bash
# 1. 创建虚拟环境并安装依赖
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 2. 配置（可选，均有默认值）
cp .env.example .env

# 3. 启动服务
.venv/bin/python -m app.main --port 8001

# 4. 测试
curl -X POST http://localhost:8001/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "你好"}'
```

浏览器打开 `http://<IP>:8001/` 使用 Web 界面，`http://<IP>:8001/docs` 查看 Swagger 文档。

---

## API 接口一览

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/chat` | 发送消息，支持流式、会话续接、重置 |
| `GET` | `/conversations` | 列出所有会话 |
| `GET` | `/conversations/{cid}` | 获取单个会话历史 |
| `DELETE` | `/conversations/{cid}` | 删除会话 |
| `POST` | `/tasks` | 创建定时任务 |
| `GET` | `/tasks` | 列出所有任务 |
| `DELETE` | `/tasks/{task_id}` | 删除任务 |
| `GET` | `/health` | 健康检查 |

---

## POST /chat — 发送消息

### 请求体（JSON）

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `message` | string | **必填** | 用户消息 |
| `backend` | string | `"claude"` | CLI 后端：`claude` / `codex` / `opencode` |
| `conversation_id` | string \| null | `null` | 会话 ID，留空则新建 |
| `reset_session` | bool | `false` | `true` = 清除 CLI session，新开上下文 |
| `stream` | bool | `false` | `true` = 返回 SSE 流式响应 |
| `model` | string \| null | `null` | 覆盖 CLI 默认模型 |
| `max_turns` | int | `10` | 最大对话轮次 |
| `work_dir` | string \| null | `null` | 覆盖 CLI 工作目录（默认 `~`） |
| `system_prompt` | string \| null | `null` | 自定义系统提示词 |

### 响应体（JSON）

```json
{
  "conversation_id": "a1b2c3d4",
  "message": "AI 的回复文本",
  "model": "",
  "usage": {
    "input_tokens": 12000,
    "output_tokens": 350
  },
  "tool_calls": [
    {
      "type": "call",
      "name": "Bash",
      "input": {"command": "ls -la", "description": "List files"},
      "result": "total 44\ndrwxrwxr-x ..."
    }
  ],
  "timestamp": "2026-07-30T10:00:00"
}
```

| 响应字段 | 类型 | 说明 |
|---------|------|------|
| `conversation_id` | string | 会话 ID（新建或已有） |
| `message` | string | AI 回复文本 |
| `model` | string | 实际使用的模型名 |
| `usage` | object \| null | token 用量 |
| `usage.input_tokens` | int | 输入 token 数 |
| `usage.output_tokens` | int | 输出 token 数 |
| `tool_calls` | array | 工具调用记录列表 |
| `tool_calls[].type` | string | 固定 `"call"` |
| `tool_calls[].name` | string | 工具名（`Bash`、`Read`、`write` 等） |
| `tool_calls[].input` | object | 工具输入参数 |
| `tool_calls[].result` | string | 工具执行结果 |
| `timestamp` | string | 响应时间 ISO 8601 |

### 流式响应（SSE）

请求 `stream: true` 时返回 `text/event-stream`，每行一个 JSON 事件：

```
data: {"type":"text","text":"部分回复..."}
data: {"type":"tool_use","part":{"tool":"bash",...}}
data: {"type":"result","result":"完整回复","session_id":"xxx"}
data: [DONE]
```

### 用法示例

```bash
# 基本对话
curl -X POST http://localhost:8001/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "帮我写一首诗"}'

# 指定后端
curl -X POST http://localhost:8001/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "分析这段代码", "backend": "codex"}'

# 继续已有对话（自动续接 CLI session）
curl -X POST http://localhost:8001/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "再详细说说", "conversation_id": "a1b2c3d4"}'

# 重置会话（新开 CLI session，清除上下文）
curl -X POST http://localhost:8001/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "新话题", "conversation_id": "a1b2c3d4", "reset_session": true}'

# 流式输出
curl -N -X POST http://localhost:8001/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "解释量子计算", "stream": true}'
```

---

## GET /conversations — 会话列表

### 响应

```json
[
  {
    "id": "a1b2c3d4",
    "title": "帮我写一首诗",
    "backend": "claude",
    "message_count": 6,
    "created_at": "2026-07-30T10:00:00",
    "updated_at": "2026-07-30T10:15:00"
  }
]
```

---

## GET /conversations/{cid} — 会话历史

### 查询参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `limit` | int | `50` | 返回消息条数上限 |

### 响应

```json
{
  "id": "a1b2c3d4",
  "title": "帮我写一首诗",
  "backend": "claude",
  "messages": [
    {"role": "user", "content": "帮我写一首诗", "timestamp": "..."},
    {"role": "assistant", "content": "好的，这是一首...", "timestamp": "...", "tool_calls": [...]}
  ],
  "created_at": "...",
  "updated_at": "..."
}
```

---

## DELETE /conversations/{cid} — 删除会话

### 响应

```json
{"ok": true, "message": "已删除会话 a1b2c3d4"}
```

---

## POST /tasks — 创建定时任务

### 请求体

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `name` | string | **必填** | 任务名称 |
| `type` | string | **必填** | `"once"` / `"cron"` / `"interval"` |
| `schedule` | string | **必填** | ISO 时间 / cron 表达式 / 间隔 `"30m"` |
| `prompt` | string | **必填** | 触发时发送给助理的消息 |
| `enabled` | bool | `true` | 是否启用 |

### 响应

```json
{
  "ok": true,
  "task": {
    "id": "b1c2d3e4",
    "name": "早报",
    "type": "cron",
    "schedule": "0 8 * * *",
    "prompt": "帮我总结今日新闻",
    "enabled": true,
    "created_at": "...",
    "last_run": null
  }
}
```

---

## GET /tasks — 任务列表

### 响应

```json
[
  {
    "id": "b1c2d3e4",
    "name": "早报",
    "type": "cron",
    "schedule": "0 8 * * *",
    "prompt": "帮我总结今日新闻",
    "enabled": true,
    "created_at": "...",
    "last_run": null
  }
]
```

---

## DELETE /tasks/{task_id} — 删除任务

### 响应

```json
{"ok": true, "message": "已删除任务 b1c2d3e4"}
```

---

## GET /health — 健康检查

### 响应

```json
{
  "status": "ok",
  "assistant": "小助手",
  "backends": ["claude", "codex", "opencode"],
  "uptime_seconds": 120.5,
  "conversations": 5,
  "tasks": 2
}
```

---

## 会话续接（Session Continuity）

API 自动管理各后端 CLI 的 session，实现跨请求上下文记忆：

1. **首次对话** → CLI 无 `--resume` → 捕获 `session_id` 存入存储
2. **继续对话** → 读取 `session_id` → 传入 `--resume`（claude）/ `resume`（codex）/ `-s`（opencode）
3. **重置会话** → `reset_session: true` → 清空 session_id → 新开 CLI session

会话按后端独立存储，切换后端互不干扰。

---

## 后端对比

| 后端 | CLI 命令 | 工具调用 | Session 续接 |
|------|---------|---------|-------------|
| **claude** | `claude -p "..." --output-format stream-json --verbose` | ✅ Bash/Read/Write 等 | `--resume <id>` |
| **codex** | `codex exec --json --skip-git-repo-check "..."` | ✅ 需 git 仓库 | `resume <id>` |
| **opencode** | `opencode run --format json --dir <cwd> "..."` | ✅ bash/read 等 | `-s <id>` |

---

## 配置项（.env）

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `HOST` | 监听地址 | `0.0.0.0` |
| `PORT` | 监听端口 | `8000` |
| `ASSISTANT_NAME` | 助理名称 | `小助手` |
| `WORK_DIR` | CLI 工作目录 | `~`（用户主目录） |
| `MAX_TURNS` | 最大轮次 | `10` |
| `CLI_TIMEOUT` | CLI 超时（秒） | `300` |

---

## 技术栈

- **FastAPI** — Web 框架
- **claude / codex / opencode CLI** — AI 引擎（subprocess 调用）
- **JSON 文件** — 持久化存储
- **SSE** — 流式输出
