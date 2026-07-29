"""应用配置，从环境变量加载。"""

import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


class Settings:
    # 服务
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8000"))

    # Claude CLI
    CLAUDE_MODEL: str = os.getenv("CLAUDE_MODEL", "sonnet")
    CLAUDE_BIN: str = os.getenv("CLAUDE_BIN", "claude")
    MAX_TURNS: int = int(os.getenv("MAX_TURNS", "10"))
    CLI_TIMEOUT: int = int(os.getenv("CLI_TIMEOUT", "300"))  # 秒

    # 默认工作目录 — 当前用户主目录
    WORK_DIR: str = os.getenv("WORK_DIR", os.path.expanduser("~"))

    # 助理
    ASSISTANT_NAME: str = os.getenv("ASSISTANT_NAME", "小助手")

    # 持久化
    MEMORY_DIR: Path = BASE_DIR / "memory"


settings = Settings()
settings.MEMORY_DIR.mkdir(parents=True, exist_ok=True)
