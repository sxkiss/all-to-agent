# Claude 个人助理 Agent — 项目规划

## 一、项目概述

将本机已安装的 `claude` CLI 封装为 HTTP API 服务，实现一个可通过外��访问的个人助理 Agent。

**核心思路**：不直接调用 Anthropic API，而是通过 `subprocess` 调用本机 `claude` CLI（已登录、已配置好模型、hooks、MCP 等），复用其全部能力。API 只做透传。

---

## 二、技术架构

```
外部客户端 (curl / 浏览器 / 机器人)
        |
        v
+---------------------------+
|   FastAPI 服务器           |
|   app/main.py             |
|                           |
|   routers/chat.py         |  <-- POST /chat
|   routers/conversations.py|  <-- 会话管理
|   routers/tasks.py        |  <-- 定时任务
|   routers/health.py       |  <-- 健康检查
|                           |
|   agent.py                |  <-- subprocess 调�� claude CLI
|   models.py               |  <-- Pydantic 数据模型
|   config.py               |  <-- 读取 .env 配置
|   memory/store.py         |  <-- JSON 文件持久化
+-------------+-------------+
              |
              v
+---------------------------+
|  本机 claude CLI           |
|  claude -p "..."          |
|    --output-format json   |
+---------------------------+
```

---

## 三、文件结构

```
all to agent/
├── .env                    # 环境变量
├── .env.example            # 环境变量模板
├── requirements.txt        # Python 依赖
├── README.md               # 使用文档
├── ARCHITECTURE.md         # 本文档
│
├── app/
│   ├── __init__.py
│   ├── main.py             # FastAPI 入口
│   ├── config.py           # 配置
│   ├── agent.py            # 核心：调用 claude CLI
│   ├── models.py           # 数据模���
│   └── routers/
│       ├── __init__.py
│       ├── chat.py         # 对话接口
│       ├── conversations.py
│       ├── tasks.py
│       └── health.py
│
└── memory/
    ├── __init__.py
    ├── store.py            # JSON 持久化存储
    ├── conversations.json  # 运行时生成
    └── tasks.json          # 运行时生成
```

---

## 四、核心功能

### 4.1 对话接口

| 接口 | 方法 | 说明 |
|------|------|------|
| `/chat` | POST | 发送消息，返回 AI 回复 |
| `/chat` | POST | `stream=true` 时返回 SSE 流 |
| `/conversations` | GET | 列出所有会话 |
| `/conversations/{id}` | GET | 获取会话历史 |
| `/conversations/{id}` | DELETE | 删除会话 |

### 4.2 定时任务

| 接口 | 方法 | 说明 |
|------|------|------|
| `/tasks` | POST | 创建定时任务 |
| `/tasks` | GET | 列出所有任务 |
| `/tasks/{id}` | DELETE | 删除任务 |

### 4.3 健康检查

`GET /health` 返回服务状态、运行时间、活跃任务数。

---

## 五、调�� claude CLI 的关键设���

```python
# 同步调用 — 返回完整 JSON
result = subprocess.run(
    ["claude", "-p", prompt, "--output-format", "json"],
    capture_output=True, text=True, timeout=300,
)

# 流式调用 — ��行读取 stream-json，转为 SSE
process = subprocess.Popen(
    ["claude", "-p", prompt, "--output-format", "stream-json"],
    stdout=subprocess.PIPE, text=True,
)
for line in process.stdout:
    yield f"data: {line}\n\n"
```

所有 Claude 的能���（工具调用、MCP、hooks、模型选择）都由 CLI 自身提供，API 无需额外处理。

---

## 六、快速启动

```bash
# 安装
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 配置
cp .env.example .env

# 启动
.venv/bin/python -m app.main

# 测试
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "你好"}'
```
