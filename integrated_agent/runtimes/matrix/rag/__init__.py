"""知识库 RecordStore 与文档门面。写帖 / 回评 / 召回聊天共用检索，不含业务 Flow。"""

from .knowledge import KnowledgeStore

__all__ = ["KnowledgeStore"]
