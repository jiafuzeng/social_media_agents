from __future__ import annotations

from pathlib import Path
from typing import Any


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


def install_scripted_ask(monkeypatch, model) -> None:
    """把 chunk 里的 Agently.create_agent 接到 ScriptedMatrixModel，避免测试打真实模型。"""

    class FakeAgent:
        def __init__(self, name: str) -> None:
            self._name = name
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

        async def async_start(self):
            input_data = self._input or {}
            info = self._info or {}
            snapshot = info.get("snapshot") if isinstance(info, dict) else info
            context = info.get("context") if isinstance(info, dict) else info
            name = self._name
            if name == "matrix-compose-brief":
                return await model.compose_brief(
                    text=input_data["text"], info=snapshot
                )
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
                return await model.reply_brief(
                    text=input_data["text"], info=snapshot
                )
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

    def fake_create_agent(*args, **kwargs):
        name = kwargs.get("name")
        if not name:
            for item in args:
                if isinstance(item, str):
                    name = item
                    break
        return FakeAgent(str(name or ""))

    monkeypatch.setattr(
        "integrated_agent.runtimes.matrix.analysis.capability.load_model_settings",
        lambda: None,
    )
    monkeypatch.setattr(
        "integrated_agent.runtimes.matrix.analysis.workflows.chunks.compose.pipeline.Agently.create_agent",
        fake_create_agent,
    )
    monkeypatch.setattr(
        "integrated_agent.runtimes.matrix.analysis.workflows.chunks.reply.pipeline.Agently.create_agent",
        fake_create_agent,
    )
