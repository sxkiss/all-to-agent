# Claude / Codex / OpenCode 个人助理 Agent — 架构文档

## 一、项目概述

将本机 `claude` / `codex` / `opencode` 三个 AI CLI 统一封装为 HTTP API，提供对话、会话管理、工具调用反馈、Web 界面等能力。

**核心设计**：不直接调用 LLM API，而是通过 `subprocess` 调用本机已安装的 CLI，复用其全部工具、MCP、hooks 和模型配置。

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
│   │   后端路由              │   │
│   │                         │   │
│   │   claude ──────────────►├───┼──► claude -p "..." --output-format stream-json
│   │   codex  ──────────────►├───┼──► codex exec --json "..."
│   │   opencode ────────────►├───┼──► opencode run --format json --dir ...
│   │                         │   │
│   │   各自 JSONL 解析器     │   │
│   └─────────────────────────┘   │
│                                 │
│        memory/store.py          │
│   JSON 文件持久化：对话、任务     │
└─────────────────────────────────┘
```

---

## 三、多后端设计

三个 CLI 输出格式各不相同，agent.py 内部使用统一的解析接口：

### Claude（stream-json）

```json
{"type":"assistant", "message":{"content":[{"type":"tool_use","name":"Bash","input":{...}}]}}
{"type":"user",      "message":{"content":[{"type":"tool_result","content":"..."}]}}
{"type":"result",    "result":"最终回复", "usage":{"input_tokens":12000,"output_tokens":350}}
```

### Codex（JSONL）

```json
{"type":"item.completed", "item":{"type":"agent_message","text":"回复内容"}}
{"type":"item.completed", "item":{"type":"tool_call","name":"Bash","arguments":{...}}}
{"type":"turn.completed","usage":{"input_tokens":12000,"output_tokens":350}}
```

### OpenCode（JSON）

```json
{"type":"text",      "part":{"text":"回复内容"}}
{"type":"tool_use",  "part":{"tool":"bash","state":{"input":{...},"output":"结果"}}}
{"type":"step_finish","part":{"tokens":{"input":12000,"output":350}}}
```

每个后端有独立的 `_parse_event_*` 函数，统一输出：
```python
{"result": "文本", "tool_calls": [...], "usage": Usage(...)}
```

---

## 四、对话与后端绑定

- 新建对话时记录所用 `backend` 字段
- 加载历史对话时，前端自动切回对应的后端按钮
- 对话列表显示后端标签（颜色区分）

---

## 五、工具调用反馈

API 响应中包含 `tool_calls` 数组：

```json
{
  "tool_calls": [
    {
      "type": "call",
      "name": "Bash",
      "input": {"command": "ls -la", "description": "List files"},
      "result": "total 44\ndrwxrwxr-x ..."
    }
  ]
}
```

Web 界面中，工具调用以可折叠卡片形式展示在回复上方。

---

## 六、文件结构

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
│   ├── agent.py               # 核心：多后端 CLI 调度 + 解析
│   ├── models.py              # Pydantic 数据模型
│   ├── static/
│   │   └── index.html         # Web 聊天界面
│   └── routers/
│       ├── chat.py            # POST /chat
│       ├── conversations.py   # 会话管理
│       ├── tasks.py           # 定时任务
│       └── health.py          # 健康检查
│
└── memory/
    ├── store.py               # JSON 持久化
    ├── conversations.json     # 运行时生成
    └── tasks.json             # 运行时生成
```

---

## 七、配置

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `HOST` | 监听地址 | `0.0.0.0` |
| `PORT` | 端口 | `8000` |
| `WORK_DIR` | CLI 工作目录 | `~`（用户主目录） |
| `ASSISTANT_NAME` | 助理名 | `小助手` |
| `MAX_TURNS` | 最大轮次 | `10` |
| `CLI_TIMEOUT` | 超时（秒） | `300` |

---

## 八、快速启动

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
.venv/bin/python -m app.main --port 8001
```

访问 `http://<IP>:8001` 使用 Web 界面，或直接调用 REST API。
