from __future__ import annotations

import os

import uvicorn

from integrated_agent.bootstrap.service import create_production_app

if not os.environ.get("DEEPSEEK_API_KEY"):
    raise RuntimeError(
        "缺少 DEEPSEEK_API_KEY。请复制 .env.example 为 .env"
        "填写模型密钥后再启动问数服务。"
    )

# 启动时打印实际生效的模型端点（不含密钥），便于核对 .env 是否被旧环境变量盖住。
print(
    f"[matrix] model={os.environ.get('DEEPSEEK_DEFAULT_MODEL')!s} "
    f"base_url={os.environ.get('DEEPSEEK_BASE_URL')!s}",
    flush=True,
)

app = create_production_app()


if __name__ == "__main__":
    uvicorn.run(
        app,
        host=os.environ.get("APP_HOST", "127.0.0.1"),
        port=int(os.environ.get("APP_PORT", "8000")),
    )
