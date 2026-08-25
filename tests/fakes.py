from __future__ import annotations

from pathlib import Path
from typing import Any

from tests.scripted_compose import ScriptedComposeModel
from tests.scripted_reply import ScriptedReplyModel
from integrated_agent.runtimes.matrix.host.models import CommentIn


class ScriptedMatrixModel(ScriptedComposeModel, ScriptedReplyModel):
    """HTTP 应用同时挂写帖和回评时的测试门面，业务包不要依赖。"""


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

    def use_actions(self, actions):
        del actions
        return self

    def set_action_loop(self, **kwargs):
        del kwargs
        return self

    def activate_session(self, *, session_id: str | None = None):
        self.session_id = session_id
        return self

    async def async_start(self, **kwargs):
        del kwargs
        return await self._dispatch(
            self._name,
            self._input or {},
            self._info or {},
            getattr(self, "session_id", None),
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


async def _dispatch_compose_reply(
    model, name: str, input_data: Any, info: Any, session_id: str | None = None
):
    if hasattr(model, "agent_sessions"):
        model.agent_sessions.append((name, session_id))
    snapshot = info.get("snapshot") if isinstance(info, dict) else info
    if name == "matrix-compose-route-intent":
        return await model.route_intent(text=input_data.get("text") or "", info=info)
    if name == "matrix-compose-intel-react":
        cleaned = info.get("已完成步骤") if isinstance(info, dict) else []
        need_trends = bool((info or {}).get("need_trends")) if isinstance(info, dict) else False
        has_trend = any(
            isinstance(item, dict) and str(item.get("kind") or "").strip().lower() == "trend"
            for item in (cleaned or [])
        )
        has_tweet = any(
            isinstance(item, dict)
            and str(item.get("kind") or "").strip().lower() == "tweet"
            and str(item.get("tweet_id") or "").strip()
            for item in (cleaned or [])
        )
        if need_trends and not has_trend:
            return {
                "type": "tool",
                "reasoning": "前端开了趋势，先拉热搜。",
                "tool_calls": [{"name": "fetch_trending", "args": {}}],
                "answer": "",
            }
        if not has_tweet:
            keyword = str(
                (info or {}).get("user_instruction")
                or input_data
                or "创作"
            ).strip()[:40]
            return {
                "type": "tool",
                "reasoning": "需要搜索带配图的对标推文。",
                "tool_calls": [
                    {
                        "name": "fetch_search_timeline",
                        "args": {
                            "keyword": keyword or "创作",
                            "search_type": "Media",
                        },
                    }
                ],
                "answer": "",
            }
        return {
            "type": "final",
            "reasoning": "已有推文/趋势观察，可进 Brief。",
            "tool_calls": [],
            "answer": "已采集创作素材卡",
        }
    if name == "matrix-compose-intel-plan":
        text = input_data if isinstance(input_data, str) else str(input_data or "")
        post_count = 1
        if isinstance(info, dict):
            try:
                post_count = max(1, int(info.get("post_count") or 1))
            except (TypeError, ValueError):
                post_count = 1
        tasks = [
            {
                "task_id": f"m{index}",
                "goal": f"{text or '创作素材'}（角度 {index}）",
            }
            for index in range(1, post_count + 1)
        ]
        return {
            "plan_summary": "测试素材计划",
            "tasks": tasks,
        }
    if name == "matrix-compose-intel-task":
        goal = ""
        if isinstance(input_data, dict):
            goal = str(input_data.get("goal") or "")
        tweet_id = str(abs(hash(goal or "x")) % 10**15)
        return {
            "answer": "已采集测试推文素材",
            "material_list": [
                {
                    "kind": "tweet",
                    "title": "test_user",
                    "text": goal or "测试推文正文",
                    "link": f"https://x.com/test_user/status/{tweet_id}",
                    "tweet_id": tweet_id,
                    "screen_name": "test_user",
                    "media_links": [
                        {
                            "type": "photo",
                            "thumb": "https://pbs.twimg.com/media/test.jpg",
                            "preview_url": "https://pbs.twimg.com/media/test.jpg",
                        }
                    ],
                    "media": [
                        {
                            "type": "photo",
                            "thumb": "https://pbs.twimg.com/media/test.jpg",
                        }
                    ],
                }
            ],
        }
    if name == "matrix-compose-original-draft":
        work = info.get("work_item") if isinstance(info, dict) else {}
        draft_key = str((work or {}).get("draft_key") or "d1")
        draft_index = str((work or {}).get("draft_index") or draft_key.lstrip("d") or "1")
        offered = info.get("offered_media") if isinstance(info, dict) else []
        media_token = " [[media:m1]]" if offered else ""
        return {
            "draft_text": f"秋季上新草稿{draft_index}，详情见官方说明。{media_token}".strip(),
            "rationale": f"测试草稿 {draft_key}。",
        }
    if name == "matrix-compose-rewrite-draft":
        work = info.get("work_item") if isinstance(info, dict) else {}
        draft_key = str((work or {}).get("draft_key") or "d1")
        draft_index = str((work or {}).get("draft_index") or draft_key.lstrip("d") or "1")
        offered = info.get("offered_media") if isinstance(info, dict) else []
        media_token = " [[media:m1]]" if offered else ""
        return {
            "draft_text": f"改写草稿{draft_index}，已按我们口吻调整。{media_token}".strip(),
            "rationale": f"测试改写草稿 {draft_key}。",
        }
    if name == "matrix-compose-source-react":
        cleaned = info.get("已完成步骤") if isinstance(info, dict) else []
        has_tweet_cards = any(
            isinstance(item, dict)
            and str(item.get("kind") or "").strip().lower() == "tweet"
            and str(item.get("tweet_id") or "").strip()
            for item in (cleaned or [])
        )
        if has_tweet_cards:
            return {
                "type": "final",
                "reasoning": "已完成步骤中已有推文素材卡。",
                "tool_calls": [],
                "answer": "已拿到推文原文包。",
            }
        anchor = str((info or {}).get("source_anchor") or "").strip()
        if anchor.isdigit():
            return {
                "type": "tool",
                "reasoning": "需拉取推文详情以生成素材卡。",
                "tool_calls": [
                    {"name": "fetch_tweet_detail", "args": {"tweet_id": anchor}}
                ],
                "answer": "",
            }
        question = str(input_data or "").strip()
        return {
            "type": "tool",
            "reasoning": "需搜索推文以生成素材卡。",
            "tool_calls": [
                {
                    "name": "fetch_search_timeline",
                    "args": {"keyword": question or "改写", "search_type": "Latest"},
                }
            ],
            "answer": "",
        }
    if name == "matrix-compose-brief":
        text = input_data.get("text") if isinstance(input_data, dict) else str(input_data or "")
        result = await model.compose_brief(
            text=text,
            info=info if isinstance(info, dict) else {},
        )
        return result.model_dump(mode="json") if hasattr(result, "model_dump") else result
    if name == "matrix-compose-draft":
        merged_info = dict(info) if isinstance(info, dict) else {}
        payload = input_data if isinstance(input_data, dict) else {}
        merged_info.update(
            {
                key: value
                for key, value in payload.items()
                if key not in {"work_item", "repair"}
            }
        )
        result = await model.compose_draft(
            work_item=payload["work_item"],
            info=merged_info,
            repair=payload.get("repair") or None,
        )
        return result.model_dump(mode="json") if hasattr(result, "model_dump") else result
    if name == "matrix-compose-review":
        result = await model.compose_review(
            package=input_data["package"], info=snapshot
        )
        return result.model_dump(mode="json") if hasattr(result, "model_dump") else result
    if name == "matrix-reply-brief":
        return await model.reply_brief(text=input_data["text"], info=snapshot)
    if name == "matrix-reply-draft":
        return await model.reply_draft(
            work_item=input_data["work_item"],
            info=info if isinstance(info, dict) else {},
            repair=input_data.get("repair") or None,
        )
    if name == "matrix-reply-review":
        draft = input_data.get("draft") if isinstance(input_data, dict) else input_data
        return await model.reply_review(draft=draft)
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


def install_compose_ask(monkeypatch, model) -> None:
    """只替换写帖 pipeline 的 Agently.create_agent。"""

    async def dispatch(
        name: str, input_data: Any, info: Any, session_id: str | None = None
    ):
        return await _dispatch_compose_reply(
            model, name, input_data, info, session_id
        )

    async def fake_run_one_tool(name, args, tools):
        del tools
        tweet_id = str((args or {}).get("tweet_id") or "1234567890123456789")
        if name == "fetch_tweet_detail":
            return {
                "tool": name,
                "args": args or {},
                "result": {
                    "code": 200,
                    "data": {
                        "tweet_id": tweet_id,
                        "text": "原帖正文供改写使用",
                        "screen_name": "demo",
                        "media": [
                            {
                                "type": "photo",
                                "thumb": "https://pic.example.com/source.jpg",
                                "width": 1200,
                                "height": 675,
                            }
                        ],
                    },
                },
            }
        if name == "fetch_search_timeline":
            return {
                "tool": name,
                "args": args or {},
                "result": {
                    "code": 200,
                    "data": {
                        "timeline": [
                            {
                                "tweet_id": "9876543210987654321",
                                "text": "搜索到的参考推文",
                                "screen_name": "search_demo",
                                "media": [
                                    {
                                        "type": "photo",
                                        "thumb": "https://pbs.twimg.com/media/ref.jpg",
                                    }
                                ],
                            }
                        ]
                    },
                },
            }
        if name == "fetch_trending":
            return {
                "tool": name,
                "args": args or {},
                "result": {
                    "code": 200,
                    "data": {
                        "trends": [
                            {
                                "name": "秋季上新",
                                "context": "China",
                                "description": "测试热搜",
                            }
                        ]
                    },
                },
            }
        return {
            "tool": name,
            "args": args or {},
            "result": {"error": f"unexpected tool in test: {name}"},
        }

    monkeypatch.setattr(
        "integrated_agent.runtimes.matrix.compose.route.Agently.create_agent",
        _fake_create_agent(dispatch),
    )
    fake_agent = _fake_create_agent(dispatch)
    for module in (
        "integrated_agent.runtimes.matrix.compose.brief",
        "integrated_agent.runtimes.matrix.compose.draft_gate",
        "integrated_agent.runtimes.matrix.compose.intel",
        "integrated_agent.runtimes.matrix.compose.review",
        "integrated_agent.runtimes.matrix.compose.source",
    ):
        monkeypatch.setattr(f"{module}.Agently.create_agent", fake_agent)
    monkeypatch.setattr(
        "integrated_agent.runtimes.matrix.compose.source._run_one_tool",
        fake_run_one_tool,
    )
    monkeypatch.setattr(
        "integrated_agent.runtimes.matrix.compose.intel._run_one_tool",
        fake_run_one_tool,
    )


def install_reply_ask(monkeypatch, model) -> None:
    """只替换回评 pipeline 的 Agently.create_agent。"""

    async def dispatch(
        name: str, input_data: Any, info: Any, session_id: str | None = None
    ):
        return await _dispatch_compose_reply(
            model, name, input_data, info, session_id
        )

    monkeypatch.setattr(
        "integrated_agent.runtimes.matrix.reply.pipeline.Agently.create_agent",
        _fake_create_agent(dispatch),
    )


def install_scripted_ask(monkeypatch, model) -> None:
    """HTTP 应用同时挂写帖和回评时一并替换，不碰召回聊天。"""

    install_compose_ask(monkeypatch, model)
    install_reply_ask(monkeypatch, model)


def install_kb_chat_ask(monkeypatch, model) -> None:
    """只替换召回聊天 pipeline 的 Agently.create_agent，不碰写帖 / 回评。"""

    async def dispatch(name: str, input_data: Any, info: Any):
        return await _dispatch_kb_chat(model, name, input_data, info)

    monkeypatch.setattr(
        "integrated_agent.runtimes.matrix.kb_chat.pipeline.Agently.create_agent",
        _fake_create_agent(dispatch),
    )
