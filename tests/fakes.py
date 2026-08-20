from __future__ import annotations

from pathlib import Path
from typing import Any

from integrated_agent.runtimes.matrix.models import CommentIn

class EmptyKnowledgeStore:
    """HTTP / Flow 测试用：写稿 RetrieveKb 返回空卡，避免打真实 embedding。"""

    async def retrieve_draft_cards(self, *args, **kwargs):
        del args, kwargs
        return []


DEMO_REPLY_COMMENTS = [
    CommentIn(
        comment_key="c1",
        text="这个真的稳赚吗？买了能翻倍吗？",
        role="root",
        author_display="用户甲",
    ),
    CommentIn(
        comment_key="c2",
        text="你们就是骗子，滚出这个平台",
        role="root",
        author_display="用户乙",
    ),
    CommentIn(
        comment_key="c3",
        text="成分表在哪看？有没有官方说明？",
        role="root",
        author_display="用户丙",
    ),
]


async def fake_question_runner(
    question: str,
    *,
    task_id: str,
    output_directory: Path,
) -> dict[str, Any]:
    del question
    output_directory.mkdir(parents=True, exist_ok=True)
    (output_directory / "run.json").write_text("{}", encoding="utf-8")
    return {
        "task_id": task_id,
        "status": "completed",
        "data_snapshot_id": "lesson23-analysis-b7ad59fddab30331",
        "final_answer": {
            "answer": "2025 年 618 规模增长，但缺货损失上升，增长质量需要继续观察。",
            "claims": [
                {
                    "claim_id": "claim-1",
                    "text": "2025 年 618 的净营收高于 2024 年。",
                    "evidence_ids": ["evidence-1"],
                }
            ],
        },
        "evidence": [
            {
                "evidence_id": "evidence-1",
                "query_id": "query-1",
                "result_ref": (
                    "sqlite-result://lesson23-analysis-b7ad59fddab30331/query-1"
                ),
                "summary": "返回两年 618 净营收对比。",
                "analysis_goal": "比较 618 经营规模",
                "columns": [
                    "paid_gmv_cents_2024",
                    "paid_gmv_cents_2025",
                    "net_revenue_cents_2024",
                    "net_revenue_cents_2025",
                ],
                "rows_preview": [
                    {
                        "paid_gmv_cents_2024": 249_810_000,
                        "paid_gmv_cents_2025": 293_020_000,
                        "net_revenue_cents_2024": 231_830_000,
                        "net_revenue_cents_2025": 273_690_000,
                    }
                ],
                "column_semantics": {
                    column: {
                        "kind": "currency",
                        "storage_unit": "人民币分",
                        "display_unit": "人民币元或万元",
                    }
                    for column in [
                        "paid_gmv_cents_2024",
                        "paid_gmv_cents_2025",
                        "net_revenue_cents_2024",
                        "net_revenue_cents_2025",
                    ]
                },
            }
        ],
    }


class _ScriptedAgent:
    """测试替身：只转发 input / info，由 install_* 决定接到哪套模型。"""

    def __init__(self, name: str, dispatch) -> None:
        self._name = name
        self._dispatch = dispatch
        self._input: Any = None
        self._info: Any = None

    def input(self, data):
        self._input = data
        return self

    def info(self, data):
        self._info = data
        return self

    def instruct(self, instruct):
        del instruct
        return self

    def output(self, schema, format="json"):
        del schema, format
        return self

    def activate_session(self, *, session_id: str | None = None):
        self.session_id = session_id
        return self

    async def async_start(self):
        return await self._dispatch(
            self._name, self._input or {}, self._info or {}
        )


def _fake_create_agent(dispatch):
    def fake_create_agent(*args, **kwargs):
        name = kwargs.get("name")
        if not name:
            for item in args:
                if isinstance(item, str):
                    name = item
                    break
        return _ScriptedAgent(str(name or ""), dispatch)

    return fake_create_agent


async def _dispatch_compose_reply(model, name: str, input_data: Any, info: Any):
    snapshot = info.get("snapshot") if isinstance(info, dict) else info
    context = info.get("context") if isinstance(info, dict) else info
    if name == "matrix-compose-brief":
        return await model.compose_brief(text=input_data["text"], info=snapshot)
    if name == "matrix-compose-draft":
        return await model.compose_draft(
            work_item=input_data["work_item"],
            info=context,
            repair=input_data.get("repair") or None,
        )
    if name == "matrix-compose-review":
        return await model.compose_review(
            package=input_data["package"], info=snapshot
        )
    if name == "matrix-reply-brief":
        return await model.reply_brief(text=input_data["text"], info=snapshot)
    if name == "matrix-reply-draft":
        return await model.reply_draft(
            work_item=input_data["work_item"],
            info=context,
            repair=input_data.get("repair") or None,
        )
    if name == "matrix-reply-review":
        return await model.reply_review(
            package=input_data["package"], info=snapshot
        )
    raise AssertionError(f"unexpected Agently agent name: {name}")


async def _dispatch_kb_chat(model, name: str, input_data: Any, info: Any):
    if name == "kb-chat-rewrite":
        return await model.kb_chat_rewrite(
            query=input_data.get("query") or "",
            history=input_data.get("history") or [],
        )
    if name == "kb-chat-split":
        return await model.kb_chat_split(
            rewritten_query=input_data.get("rewritten_query") or "",
            history=input_data.get("history") or [],
        )
    if name == "kb-chat-analyze":
        return await model.kb_chat_analyze(
            query=input_data.get("query") or "",
            info=info if isinstance(info, dict) else {},
            history=input_data.get("history") or [],
        )
    if name in {"kb-chat-summarize", "kb-chat"}:
        return await model.kb_chat(
            query=input_data.get("query") or "",
            info=info if isinstance(info, dict) else {},
            history=input_data.get("history") or [],
            analysis=input_data.get("analysis") or {},
        )
    raise AssertionError(f"unexpected Agently agent name: {name}")


def install_scripted_ask(monkeypatch, model) -> None:
    """只替换写帖 / 回评 chunk 的 Agently.create_agent，不碰召回聊天。"""

    async def dispatch(name: str, input_data: Any, info: Any):
        return await _dispatch_compose_reply(model, name, input_data, info)

    fake_create_agent = _fake_create_agent(dispatch)
    monkeypatch.setattr(
        "integrated_agent.runtimes.matrix.analysis.workflows.chunks.compose.pipeline.Agently.create_agent",
        fake_create_agent,
    )
    monkeypatch.setattr(
        "integrated_agent.runtimes.matrix.analysis.workflows.chunks.reply.pipeline.Agently.create_agent",
        fake_create_agent,
    )


def install_kb_chat_ask(monkeypatch, model) -> None:
    """只替换召回聊天 pipeline 的 Agently.create_agent，不碰写帖 / 回评。"""

    async def dispatch(name: str, input_data: Any, info: Any):
        return await _dispatch_kb_chat(model, name, input_data, info)

    monkeypatch.setattr(
        "integrated_agent.runtimes.matrix.kb_chat.pipeline.Agently.create_agent",
        _fake_create_agent(dispatch),
    )
