"""FastAPI 入口"""

import logging
import logging.handlers
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.config import settings
from app.routers import chat, conversations, tasks, health

# 日志输出到项目目录 logs/ 下，按大小自动轮转（10MB保留5个）
LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
_log_file = LOG_DIR / "agent-api.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.handlers.RotatingFileHandler(
            _log_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
        ),
        logging.StreamHandler(),  # 同时输出到终端
    ],
)
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)  # 关掉 access log 刷屏

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.info("Server started at http://%s:%s", settings.HOST, settings.PORT)
    yield
    logging.info("Server stopped")


app = FastAPI(
    title="Claude Agent API",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(chat.router)
app.include_router(conversations.router)
app.include_router(tasks.router)


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(STATIC_DIR / "index.html")


if __name__ == "__main__":
    import sys
    import uvicorn

    port = settings.PORT
    for i, arg in enumerate(sys.argv):
        if arg == "--port" and i + 1 < len(sys.argv):
            port = int(sys.argv[i + 1])

    uvicorn.run("app.main:app", host=settings.HOST, port=port, timeout_keep_alive=0)
