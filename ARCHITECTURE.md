# Claude / Codex / OpenCode 个人助理 Agent — 架构文档

## 一、项目概述

将本机 `claude` / `codex` / `opencode` 三个 AI CLI 统一封装为 HTTP API，提供对话、会话续接、工具调用反馈、Web 界面等能力。

**核心设计**：不直接调用 LLM API，通过 `subprocess` 调用本机已安装的 CLI，复用其全部工具、MCP、hooks 和模型配置。

---

## 二、系统架构

```
浏览器 / 外部客户端
        │
        ▼
┌─────────────────────────────────┐
│         FastAPI 服务器           │
│        app/main.py              │
│                                 │
│   GET /              → Web UI   │
│   POST /chat         → 对话     │
│   GET /conversations → 会话列表  │
│   GET /tasks         → 定时任务  │
│   GET /health        → 健康检查  │
│                                 │
│        app/agent.py             │
│   ┌─────────────────────────┐   │
│   │   session_id 管理        │   │
│   │   首次调用 → 捕获 sid    │   │
│   │   续接   → --resume sid │   │
│   │   重置   → 清空 sid     │   │
│   │                         │   │
│   │   后端路由 + 事件解析     │   │
│   │   claude  ─► stream-json│   │
│   │   codex   ─► JSONL      │   │
│   │   opencode ─► JSON      │   │
│   └─────────────────────────┘   │
│                                 │
│        memory/store.py          │
│   conversations.json:           │
│     sessions: {                 │
│       "claude":   "sid-xxx",   │
│       "codex":    "tid-yyy",   │
│       "opencode": "ses-zzz"    │
│     }                           │
└─────────────────────────────────┘
```

---

## 三、会话续接机制

各 CLI 都有原生 session 续接功能，API 层统一管理：

| 后端 | session ID 来源 | 续接命令 |
|------|----------------|---------|
| claude | `system.init.session_id` | `--resume <id>` |
| codex | `thread.started.thread_id` | `resume <id>`（子命令） |
| opencode | `step_start.sessionID` | `-s <id>` |

### 数据流

```
新建对话：
  用户 → POST /chat (无 conversation_id)
    → store.create_conversation()
    → CLI 首次调用（无 --resume）
    → 解析 stream events，捕获 session_id
    → store.set_session_id(cid, backend, sid)
    → 返回 conversation_id + 回复

继续对话：
  用户 → POST /chat (有 conversation_id)
    → store.get_session_id(cid, backend)
    → CLI 调用带 --resume / -s / resume
    → AI 能记住上下文
    → 更新 session_id（可能变化）

重置会话：
  用户 → POST /chat (reset_session=true)
    → store.set_session_id(cid, backend, "")
    → CLI 无 --resume，新开 session
    → 捕获新 session_id 存入
```

### 各后端 session 独立

同一 conversation 中，三个后端各维护独立 session：

```
conversation ef32bed5:
  claude:   "c32113df-84f2-43d0-a532-d5281666fd08"
  codex:    "019fae6b-00ce-7c20-ad4a-a69ad1c5d619"
  opencode: "ses_05194d002ffebzuh7BgiKU16lN"
```

切换后端不会干扰其他后端的 session 上下文。

---

## 四、多后端事件解析

三个 CLI 输出格式各不相同，agent.py 内部使用独立解析器，统一输出：

```python
{
    "result": "文本",
    "tool_calls": [{"name": "...", "input": {...}, "result": "..."}],
    "usage": Usage(input_tokens=N, output_tokens=N),
    "session_id": "用于续接的 ID"
}
```

### Claude（stream-json）

```json
{"type":"system","subtype":"init","session_id":"..."}
{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Bash","input":{...}}]}}
{"type":"user","message":{"content":[{"type":"tool_result","content":"..."}]}}
{"type":"result","result":"回复","session_id":"...","usage":{...}}
```

### Codex（JSONL）

```json
{"type":"thread.started","thread_id":"..."}
{"type":"item.completed","item":{"type":"agent_message","text":"回复"}}
{"type":"item.completed","item":{"type":"tool_call","name":"Bash","arguments":{...}}}
{"type":"turn.completed","usage":{...}}
```

### OpenCode（JSON）

```json
{"type":"step_start","sessionID":"..."}
{"type":"tool_use","part":{"tool":"bash","state":{"input":{...},"output":"结果"}}}
{"type":"text","part":{"text":"回复"}}
{"type":"step_finish","part":{"tokens":{...}}}
```

---

## 五、文件结构

```
all to agent/
├── .env / .env.example        # 环境变量
├── requirements.txt           # Python 依赖
├── README.md                  # 使用文档
├── ARCHITECTURE.md            # 本文档
│
├── app/
│   ├── main.py                # FastAPI 入口 + 静态文件
│   ├── config.py              # 配置（WORK_DIR 等）
│   ├── agent.py               # 核心：多后端 CLI 调度 + session 管理 + 事件解析
│   ├── models.py              # Pydantic 数据模型
│   ├── static/
│   │   └── index.html         # Web 聊天界面
│   └── routers/
│       ├── chat.py            # POST /chat（含 session 续接）
│       ├── conversations.py   # 会话管理
│       ├── tasks.py           # 定时任务
│       └── health.py          # 健康检查
│
└── memory/
    ├── store.py               # JSON 持久化（含 session_id 存取）
    ├── conversations.json     # 运行时生成
    └── tasks.json             # 运行时生成
```

---

## 六、配置

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `HOST` | 监听地址 | `0.0.0.0` |
| `PORT` | 端口 | `8000` |
| `WORK_DIR` | CLI 工作目录 | `~`（用户主目录） |
| `ASSISTANT_NAME` | 助理名 | `小助手` |
| `MAX_TURNS` | 最大轮次 | `10` |
| `CLI_TIMEOUT` | 超时（秒） | `300` |

---

## 七、快速启动

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
.venv/bin/python -m app.main --port 8001
```

访问 `http://<IP>:8001` 使用 Web 界面，或直接调用 REST API。
