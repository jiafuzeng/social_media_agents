"""Agently Session 最小示例：同一 session 下多轮对话会带上 chat_history。

运行（需 .env 里配置 DEEPSEEK_*）：

    python tests/session/s1.py

要点：
- Session 挂在 **同一个 Agent 实例** 上；每次 `create_agent()` 都是新实例，彼此不共享历史。
- `activate_session(session_id=...)` 绑定会话；后续 `.input(...).async_start()` 会自动注入 chat_history。
- 并发写稿（for_each）不要共用 session_id，否则历史会互相污染。
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agently import Agently

import integrated_agent.config  # noqa: F401  # 加载 .env 与 model_pool

SESSION_ID = "matrix-session-demo-001"
MEMORY_ROOT = ROOT / "workspace" / "session_demo" / "records"


async def chat_turn(agent, *, user_text: str) -> str:
    """单轮请求；agent 已 activate_session，无需重复绑定。"""
    result = await (
        agent.input({"text": user_text})
        .instruct(
            [
                "根据当前对话上下文回答用户。",
                "若用户问「我刚才说了什么/我叫什么」，必须从历史里找，不要编造。",
                "回答控制在两句话以内。",
            ]
        )
        .output({"answer": (str, "not_null")}, format="json")
        .async_start(max_retries=1)
    )
    if isinstance(result, dict):
        return str(result.get("answer") or "").strip()
    return str(result).strip()


async def main() -> None:
    if not os.environ.get("DEEPSEEK_API_KEY"):
        raise SystemExit("缺少 DEEPSEEK_API_KEY，请先在 .env 配置模型密钥。")

    MEMORY_ROOT.mkdir(parents=True, exist_ok=True)

    # 1) 创建 Agent，可选挂载 RecordStore 做跨进程持久记忆
    agent = (
        Agently.create_agent(name="session-demo")
        .use_record_store(MEMORY_ROOT, mode="read_write")
        .activate_session(session_id=SESSION_ID)
    )
    session = agent.activated_session
    if session is None:
        raise RuntimeError("activate_session 失败")

    # 可选：启用 AgentlyMemory 插件（提取/压缩长期记忆）
    if session.memory is not None:
        session.use_memory(mode="AgentlyMemory")

    print(f"[session] id={SESSION_ID} memory_root={MEMORY_ROOT}")

    # 2) 第一轮：写入可被记住的事实
    answer1 = await chat_turn(agent, user_text="我叫小明，正在写中秋推文。")
    print(f"user: 我叫小明，正在写中秋推文。")
    print(f"assistant: {answer1}")

    # 3) 第二轮：同一 agent + 同一 session，模型应能看到上一轮历史
    answer2 = await chat_turn(agent, user_text="我刚才说我叫什么？")
    print(f"user: 我刚才说我叫什么？")
    print(f"assistant: {answer2}")

    history = list(session.context_window or [])
    print(f"[session] context_window turns={len(history)}")
    for index, item in enumerate(history, start=1):
        role = item.get("role") if isinstance(item, dict) else getattr(item, "role", "?")
        content = item.get("content") if isinstance(item, dict) else getattr(item, "content", "")
        preview = str(content).replace("\n", " ")
        print(f"  {index}. {role}: {preview}")


if __name__ == "__main__":
    asyncio.run(main())
