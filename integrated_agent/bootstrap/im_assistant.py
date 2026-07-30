"""企业微信助理进程的依赖组装。

把 ArtifactStore、三种 Runtime、Intent 路由与 WeCom Transport 组装为
可运行的 WeComAssistant；业务规则不写在此文件。
本文件只做「接线」，真正的收消息 / 路由 / 回传逻辑在 transports 与 gateway。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal, cast

# 企微官方 SDK：WebSocket 客户端，负责认证与收发消息
from aibot import WSClient, WSClientOptions
from dotenv import find_dotenv, load_dotenv

# Gateway：统一请求入口，按意图/命令选择 Runtime
from integrated_agent.gateway import AgentGateway
from integrated_agent.gateway.intent import DeepSeekIntentModel
# 三种可插拔运行时
from integrated_agent.runtimes.acp import AcpAgentRuntime
from integrated_agent.runtimes.agent import (
    AgentlyAgentRuntime,
    WorkspaceFileService,
)
from integrated_agent.runtimes.question.client import QuestionServiceRuntime
from integrated_agent.storage import ArtifactStore
# 企微传输层：入站适配 + 出站 Presenter + 媒体上传
from integrated_agent.transports.wecom import (
    WeComAssistant,
    WeComEventPresenter,
    WeComMediaClient,
)


# bootstrap → integrated_agent → 项目根目录
ROOT = Path(__file__).parents[2]


def build_im_assistant(root: Path = ROOT) -> WeComAssistant:
    """校验必需环境变量，组装 Gateway 与企业微信传输层。

    返回的 WeComAssistant 由 run_im_assistant.py 调用 .run() 阻塞运行。
    """
    # ---------- 1. 加载环境变量 ----------
    project_env = root / ".env"
    load_dotenv(
        project_env if project_env.is_file() else find_dotenv(usecwd=True)
    )

    # ---------- 2. 校验必填配置 ----------
    # 模型密钥：意图路由 + 通用 Agent；Bot 凭证：连企微 WebSocket
    settings = {
        "DEEPSEEK_API_KEY": os.environ.get("DEEPSEEK_API_KEY", ""),
        "WECOM_BOT_ID": os.environ.get("WECOM_BOT_ID", ""),
        "WECOM_BOT_SECRET": os.environ.get("WECOM_BOT_SECRET", ""),
    }
    missing = [name for name, value in settings.items() if not value]
    if missing:
        raise RuntimeError(
            "缺少运行配置："
            + ", ".join(missing)
            + "。请先在 .env 中填写这些值。"
        )

    # ---------- 3. 可选运行参数 ----------
    # 问数走独立 HTTP 进程（run_server.py）；默认本机 8000
    question_service_url = os.environ.get(
        "QUESTION_SERVICE_URL",
        "http://127.0.0.1:8000",
    )
    # 制品对外下载基址（Agent 生成文件后拼公网/本机 URL）；默认与问数同源
    artifact_base_url = os.environ.get(
        "ARTIFACT_BASE_URL",
        question_service_url,
    )
    # 通用 Agent 的 Python/Shell 沙盒后端：docker（默认）/ trusted_local / auto
    sandbox = cast(
        Literal["auto", "docker", "trusted_local"],
        os.environ.get("AGENT_SANDBOX", "docker"),
    )

    # ---------- 4. 企微连接与媒体客户端 ----------
    # WSClient：长连接企微，收 text/file，发 reply_stream / 文件
    client = WSClient(
        WSClientOptions(
            bot_id=settings["WECOM_BOT_ID"],
            secret=settings["WECOM_BOT_SECRET"],
        )
    )
    # 媒体上传：把本地制品变成企微可下发的文件消息
    media = WeComMediaClient(client)

    # ---------- 5. 本地文件与制品仓库 ----------
    # 已发布制品：不透明 ID，供下载与企微回传
    artifact_store = ArtifactStore(root / "workspace/artifacts")
    # 会话工作区：上传文件落盘、任务过程文件
    file_service = WorkspaceFileService(root / "workspace")

    # ---------- 6. 通用 Agently Agent Runtime ----------
    # 搜索、Skills、Actions、沙盒；生成文件写入 ArtifactStore
    native_agent = AgentlyAgentRuntime(
        artifact_store=artifact_store,
        public_base_url=artifact_base_url,
        skills_root=root / "skills",
        registry_root=root / "workspace/skills_registry",
        workspace_root=root / "workspace",
        file_service=file_service,
        sandbox=sandbox,
    )

    # ---------- 7. Gateway：意图路由 + 三种 Runtime ----------
    # auto 时 DeepSeekIntentModel 只在 agent/question 中选择；codex 仅手动切换
    gateway = AgentGateway(
        intent_model=DeepSeekIntentModel(),
        runtimes={
            "agent": native_agent,  # 本进程 Agently
            "question": QuestionServiceRuntime(question_service_url),  # HTTP 代理问数服务
            "codex": AcpAgentRuntime(),  # 外部 Codex ACP 进程
        },
    )

    # ---------- 8. 出站 Presenter ----------
    # 把 GatewayEvent 流转成企微流式文本 + 终态文件消息
    presenter = WeComEventPresenter(
        client=client,
        media=media,
        artifact_store=artifact_store,
    )

    # ---------- 9. 组装可运行的企微助理 ----------
    # WeComAssistant：注册 message 回调 → Gateway → Presenter
    return WeComAssistant(
        client=client,
        gateway=gateway,
        file_service=file_service,
        presenter=presenter,
    )
