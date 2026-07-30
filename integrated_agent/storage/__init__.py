"""已发布制品的稳定存储。

制品以不透明 artifact_id 目录保存；客户端不能通过下载 URL 访问任意服务器路径。
"""

from .artifacts import ArtifactStore, StoredArtifact

__all__ = ["ArtifactStore", "StoredArtifact"]
