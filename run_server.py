from __future__ import annotations

import os

import uvicorn

from integrated_agent.config import PROJECT_ROOT
from integrated_agent.bootstrap.service import create_production_app

if not os.environ.get("DEEPSEEK_API_KEY"):
    raise RuntimeError(
        "缺少 DEEPSEEK_API_KEY。请复制 .env.example 为 .env"
        "填写模型密钥后再启动问数服务。"
    )
app = create_production_app()


if __name__ == "__main__":
    uvicorn.run(
        app,
        host=os.environ.get("APP_HOST", "127.0.0.1"),
        port=int(os.environ.get("APP_PORT", "8000")),
    )
