# Claude 个人助理 Agent API

将本机 `claude` CLI 封装为 HTTP API，实现可通过外部访问的个人助理 Agent。

**原理**：不直接调用 Anthropic API，而是通过 `subprocess` 调用本机已配置好的 `claude` CLI。
所有 Claude 的能力（工具、MCP、hooks、模型选择）都由 CLI 自身提供，API 只做透传。

## 快速开始

```bash
# 1. 创建虚拟环境并安装依赖
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 2. 配置
cp .env.example .env

# 3. 启动服务
.venv/bin/python -m app.main

# 4. 测试
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "你好"}'
```

## API 接口

### 对话
```bash
# 同步对话
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "帮我写一首诗"}'

# 流式输出 (SSE)
curl -N -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "解释量子计算", "stream": true}'

# 继续已有对话
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "再详细说说", "conversation_id": "abc123"}'
```

### 会话管理
```bash
# 列出所有会话
curl http://localhost:8000/conversations

# 获取会话历史
curl http://localhost:8000/conversations/{id}

# 删除会话
curl -X DELETE http://localhost:8000/conversations/{id}
```

### 定时任务
```bash
# 创建任务
curl -X POST http://localhost:8000/tasks \
  -H "Content-Type: application/json" \
  -d '{"name": "早报", "type": "cron", "schedule": "0 8 * * *", "prompt": "帮我总结今日��闻"}'

# 列出任务
curl http://localhost:8000/tasks

# 删除任务
curl -X DELETE http://localhost:8000/tasks/{id}
```

### 健康检查
```bash
curl http://localhost:8000/health
```

## 配置项 (.env)

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `CLAUDE_MODEL` | 使用的模型 | `sonnet` |
| `CLAUDE_BIN` | claude CLI 路径 | `claude` |
| `HOST` | 监听地址 | `0.0.0.0` |
| `PORT` | 监听端口 | `8000` |
| `ASSISTANT_NAME` | 助理名称 | `小助手` |
| `MAX_TURNS` | 最大轮次 | `10` |
| `CLI_TIMEOUT` | CLI 超时(秒) | `300` |

## 项目结构

```
app/
├── main.py           # FastAPI 入口
├── config.py         # 配置
├── agent.py          # 核心：调用 claude CLI
├── models.py         # 数据模型
└── routers/          # API 路由
    ├── chat.py       # 对话接口
    ├── conversations.py  # 会话管理
    ├── tasks.py      # 定时任务
    └── health.py     # 健康检查
memory/               # 持久化存储（对话、任务）
```

## 技术栈

- **FastAPI** — Web 框架
- **claude CLI** — AI 引擎（subprocess 调用）
- **JSON 文件** — 持久化存储
- **SSE** — 流式输出
