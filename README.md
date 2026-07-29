# Claude / Codex / OpenCode 个人助理 Agent API

将本机 AI CLI 封装为 HTTP API，统一提供对话、会话管理、工具调用反馈、定时任务等能力。

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

浏览器打开 `http://<IP>:8001/` 即可使用 Web 界面。

---

## API 接口

### 对话

```bash
# 同步对话（默认 claude）
curl -X POST http://localhost:8001/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "帮我写一首诗"}'

# 指定后端
curl -X POST http://localhost:8001/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "分析这段代码", "backend": "codex"}'

# 流式输出（SSE）
curl -N -X POST http://localhost:8001/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "解释量子计算", "stream": true}'

# 继续已有对话（自动使用原后端）
curl -X POST http://localhost:8001/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "再详细说说", "conversation_id": "abc123"}'
```

**请求参数**

| 参数 | 类型 | 说明 |
|------|------|------|
| `message` | string | **必填**，用户消息 |
| `backend` | string | `claude`（默认）/ `codex` / `opencode` |
| `conversation_id` | string | 会话 ID，留空则新建 |
| `stream` | bool | 是否返回 SSE 流式 |
| `model` | string | 覆盖模型（可选） |
| `max_turns` | int | 最大轮次（默认 10） |
| `work_dir` | string | 覆盖工作目录（可选） |

**响应示例**

```json
{
  "conversation_id": "a1b2c3d4",
  "message": "当前目录包含以下文件...",
  "model": "",
  "usage": {"input_tokens": 12000, "output_tokens": 350},
  "tool_calls": [
    {
      "type": "call",
      "name": "Bash",
      "input": {"command": "ls -la", "description": "List files"},
      "result": "总用量 44\ndrwxrwxr-x ..."
    }
  ]
}
```

### 会话管理

```bash
curl http://localhost:8001/conversations              # 列出所有会话
curl http://localhost:8001/conversations/{id}          # 获取历史
curl -X DELETE http://localhost:8001/conversations/{id} # 删除会话
```

会话列表返回 `backend` 字段（`claude`/`codex`/`opencode`），加载历史对话时前端自动切回对应后端。

### 定时任务

```bash
curl -X POST http://localhost:8001/tasks \
  -H "Content-Type: application/json" \
  -d '{"name": "早报", "type": "cron", "schedule": "0 8 * * *", "prompt": "帮我总结今日新闻"}'

curl http://localhost:8001/tasks                       # 列出任务
curl -X DELETE http://localhost:8001/tasks/{id}        # 删除任务
```

### 健康检查

```bash
curl http://localhost:8001/health
# {"status":"ok","assistant":"小助手","model":"sonnet","uptime_seconds":120.5}
```

---

## 后端对比

| 后端 | 命令 | 工具调用 | 工作目录控制 |
|------|------|---------|------------|
| **claude** | `claude -p "..." --output-format stream-json --verbose` | ✅ Bash/Read/Write/Edit 等 | `--model` 切模型 |
| **codex** | `codex exec --json --skip-git-repo-check "..."` | ✅ 需 git 仓库环境 | `-c model="..."` 切模型 |
| **opencode** | `opencode run --format json --dir <cwd> "..."` | ✅ bash/read 等 | `-m provider/model` 切模型 |

---

## Web 界面

- **后端切换**：页面顶部 Claude / Codex / OpenCode 按钮，点击切换
- **对话列表**：左侧显示所有历史对话，附带后端标签（颜色区分）
- **工具调用**：回复中的工具调用以可折叠卡片展示，点开看命令和输出
- **继续对话**：点击历史对话，自动切回该对话对应的后端

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

## 项目结构

```
all to agent/
├── .env                    # 环境变量
├── .env.example            # 模板
├── requirements.txt        # 依赖
├── README.md               # 本文档
├── ARCHITECTURE.md         # 架构文档
│
├── app/
│   ├── main.py             # FastAPI 入口 + 静态文件服务
│   ├── config.py           # 配置
│   ├── agent.py            # 核心：统一调用三个 CLI + 解析事件
│   ├── models.py           # 数据模型
│   ├── static/
│   │   └── index.html      # Web 聊天界面
│   └── routers/
│       ├── chat.py         # 对话接口
│       ├── conversations.py
│       ├── tasks.py
│       └── health.py
│
└── memory/
    ├── store.py            # JSON 持久化存储
    ├── conversations.json  # 对话历史（运行时）
    └── tasks.json          # 定时任务（运行时）
```

## 技术栈

- **FastAPI** — Web 框架
- **claude / codex / opencode CLI** — AI 引擎（subprocess 调用）
- **JSON 文件** — 持久化存储
- **SSE** — 流式输出
