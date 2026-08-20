"""召回聊天测试用确定性模型。不混入写帖 / 回评 ScriptedMatrixModel。"""

from __future__ import annotations


class ScriptedKbChatModel:
    async def kb_chat_rewrite(
        self, *, query: str, history: list | None = None
    ) -> dict:
        del history
        return {"rewritten_query": str(query or "").strip()}

    async def kb_chat_split(
        self, *, rewritten_query: str, history: list | None = None
    ) -> dict:
        del history
        text = str(rewritten_query or "").strip()
        return {"retrieval_queries": [text] if text else []}

    async def kb_chat_analyze(
        self, *, query: str, info: dict, history: list | None = None
    ) -> dict:
        del query, history
        points: list[dict[str, str]] = []
        for index, card in enumerate(info.get("offered_kbs") or [], start=1):
            text = str(card.get("text") or "").strip()
            kb_id = str(card.get("kb_id") or f"k{index}")
            if not text:
                continue
            points.append(
                {
                    "point_id": f"p{index}",
                    "claim": text.split("。")[0] or text,
                    "kb_id": kb_id,
                }
            )
        return {"points": points, "uncovered": []}

    async def kb_chat(
        self,
        *,
        query: str,
        info: dict,
        history: list | None = None,
        analysis: dict | None = None,
    ) -> dict:
        del query, history, info
        points = list((analysis or {}).get("points") or [])
        if not points:
            return {
                "answer": "当前模型下没有检索到可用手册段落，无法根据知识库作答。",
                "cited_kb_ids": [],
            }
        parts: list[str] = []
        cited: list[str] = []
        for item in points:
            kb_id = str(item.get("kb_id") or "k1")
            claim = str(item.get("claim") or "").rstrip("。")
            if not claim:
                continue
            parts.append(f"{claim}[[kb:{kb_id}]]。")
            if kb_id not in cited:
                cited.append(kb_id)
        return {"answer": "".join(parts), "cited_kb_ids": cited}
