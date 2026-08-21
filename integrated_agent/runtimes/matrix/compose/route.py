"""M2：模型意图识别。成功 emit compose|rewrite；失败 emit PACKAGE。"""

from __future__ import annotations

from typing import Any, cast

from agently import Agently, TriggerFlowRuntimeData

from integrated_agent.runtimes.matrix.host.models import RouteIntentOut
from integrated_agent.runtimes.matrix.host.trace_log import TraceLog


async def compose_route(data: TriggerFlowRuntimeData) -> dict[str, Any]:
    request = cast(dict[str, Any], data.get_state("request") or {})
    text = str(request.get("text") or "")
    task_id = str(request.get("task_id") or "")
    trace = cast(TraceLog, data.require_resource("trace"))

    try:
        result = await (
            Agently.create_agent(name="matrix-compose-route-intent")
            .activate_session(session_id=str(data.require_resource("session_id")))
            .input({"text": text})
            .info(
                {
                    "job": "识别用户输入文本的意图：走创作还是改写。只签发一个确定结果，并写明理由。不要被「创作」「写推文」等口头词带偏。",
                }
            )
            .instruct(
                [
                    "先看有没有「被写/被跟对象」（现成帖、粘贴正文、链接、要检索的人/事最新动态），再给出唯一意图。必须选 compose 或 rewrite 之一，不要骑墙。",
                    "rewrite：依赖已有材料或检索对象再写。包括：改某条帖/粘贴正文口吻；参考某条写；查找某人/某事最新动态、新闻、近帖后再写成推文。",
                    "compose：只有抽象主题/卖点，不依赖现成帖，也不要求先查某人某事再写。例：「为秋季上新写预热稿」。",
                    "忽略表面用词：即使用户说「创作推文」「写一条」，只要任务是「先查最新动态再写」或「按某条改写」，一律 rewrite。例：「查找特朗普最新动态，创作推文」→ rewrite。",
                    "「对标这条的结构自己写、不要内容」才偏 compose；有链接或「参考这条写」偏 rewrite。",
                    "reason 用一两句话指出原文线索；intent 只填 compose 或 rewrite。",
                    "rewrite 且原文是帖：source_kind 为 url 或 tweet_id，source_anchor 填原文里的 tweet_id。paste 时 source_anchor 留空。只有人名/主题、无链接无 handle：source_kind 多为 none，source_anchor 留空。compose：source_kind 多为 none。",
                    "不得发明原文没有的 tweet_id 或 handle。user_instruction 填去掉原文或链接后的任务说明。",
                ]
            )
            .output(
                {
                    "reason": (str, "判定理由，须引用原文线索", "not_null"),
                    "intent": (str, "compose 或 rewrite，唯一结果", "not_null"),
                    "source_kind": (str, "paste、url、tweet_id、handle 或 none", "not_null"),
                    "source_anchor": (str, "tweet_id 或 handle，没有则空"),
                    "user_instruction": (str, "去掉原文后的任务说明"),
                    "confidence": (str, "high 或 low", "not_null"),
                },
                format="json",
            )
            .async_start()
        )
        routed = RouteIntentOut.model_validate(result)
    except Exception as exc:
        package = {
            "status": "failed",
            "intent": None,
            "task_type": "compose_post",
            "summary": "意图识别失败。",
            "drafts": [],
            "limitations": [f"route_intent_error:{type(exc).__name__}"],
        }
        await data.async_set_state("package", package, emit=False)
        await data.async_set_state("final_failed", True, emit=False)
        trace.log(
            layer="business",
            event_type="business.matrix.routed",
            status="failed",
            subject_id=task_id,
            facts={"limitations": package["limitations"]},
        )
        await data.async_emit("PACKAGE", package)
        return package

    instruction = str(routed.user_instruction or "").strip() or text.strip()
    await data.async_set_state("intent", routed.intent, emit=False)
    await data.async_set_state("source_kind", routed.source_kind, emit=False)
    await data.async_set_state("source_anchor", str(routed.source_anchor or "").strip(), emit=False)
    await data.async_set_state("user_instruction", instruction, emit=False)
    trace.log(
        layer="business",
        event_type="business.matrix.routed",
        status="completed",
        subject_id=task_id,
        facts={
            "intent": routed.intent,
            "reason": routed.reason,
            "source_kind": routed.source_kind,
            "source_anchor": routed.source_anchor,
        },
    )
    await data.async_emit(str(routed.intent), {"intent": routed.intent})
    return {
        "intent": routed.intent,
        "reason": routed.reason,
        "source_kind": routed.source_kind,
        "source_anchor": str(routed.source_anchor or "").strip(),
        "user_instruction": instruction,
    }


__all__ = ["compose_route"]
