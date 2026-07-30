"""问数 HTTP 服务进程入口。

加载环境变量后组装 FastAPI 应用，默认监听 APP_HOST/APP_PORT。
"""

from __future__ import annotations

import os
from pathlib import Path

import uvicorn
from dotenv import find_dotenv, load_dotenv

from integrated_agent.bootstrap.question_service import create_production_app


ROOT = Path(__file__).parent
project_env = ROOT / ".env"
# 优先使用项目根目录 .env，否则回退到向上查找
load_dotenv(
    project_env if project_env.is_file() else find_dotenv(usecwd=True)
)
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
