# MatrixCopilot RAG 计划表

私有知识库从零落到写稿召回的实践步骤。契约以制品管理、切分策略为准。LlamaIndex 只 Parser；案例 RAG 与 `[[ref:]]` 不改；Gate 功效门不因 `[[kb:]]` 放行。

| 项 | 值 |
|---|---|
| 产品 | MatrixCopilot 私有知识库 |
| 顺序 | **Step 1 → 2 → 3 → 4 → 5 → 6**（不可跳步） |
| 文档日期 | 2026-08-19 |
| 状态 | **Step 1–6 已落地** |

配套：

- 制品 CRUD / RecordStore：[MatrixCopilot-知识库制品管理.md](./MatrixCopilot-知识库制品管理.md)
- 切分：[MatrixCopilot-知识库切分策略.md](./MatrixCopilot-知识库切分策略.md)
- 产品总案：[MatrixCopilot-项目方案.md](./MatrixCopilot-项目方案.md)
- 工程评审：[MatrixCopilot-工程方案-技术评审.md](./MatrixCopilot-工程方案-技术评审.md)

写稿 RetrieveKb 落在 `retrieve_and_gate_draft`：案例失败仍 skip；知识库失败记 `kb_retrieve_failed` 继续写；只引 `[[kb:]]` 不能过功效空案例门。

---

## 计划表

| Step | 名称 | 做什么 | 不做什么 | 验收 |
|---|---|---|---|---|
| 1 | RecordStore 与 embedding 骨架 | `LocalRecordStore(MATRIX_KB_RECORD_ROOT)` 交给 `RecordStore`，库文件 `workspace/kb/records/records.db`；`embedding_profiles.yaml` + 每 id 一个 embeddings Agent；路由 provider 先绑定再 `embed_texts`；sqlite 向量同库 | `RecordStore(路径)` 裸构造；`vector_store_provider=auto`；改全局 DeepSeek chat；文档 CRUD / 页面 / 写稿 | `db_path` 父目录即配置根且不含 `.agently`；未知 profile 失败；缺配置工厂起不来 |
| 2 | 切分预览 | `integrated_agent/rag/` 六卡 Parser、`TextChunk`、预览 ≤80；`GET /api/kb/chunk-strategies`、`POST preview-chunks`、`POST extract`；鉴权同收藏夹；`semantic` 用 Step 1 embed，失败降级 `sentence` 且 `notes` 非空 | `RecordStore.put`；冷文件；切片保存 | 非法 strategy / 超 80 块 422；未登录 401；markdown 有标题路径；`sentence_window` 两列；预览不写库 |
| 3 | 文档 + 切片独立 CRUD | 门面 `knowledge.py`；冷文件 `workspace/kb/files/{user_id}/{artifact_id}/`；documents CRUD + 原件；单块 POST/PATCH/DELETE 当场 embed+`put`/归档；一块失败不影响兄弟块；切片锁定**文档** profile；挂 `matrix_api` / `matrix_service` | 知识库页；Draft；改 Gate；身份库 `kb_documents`；单块换 embedding | 跨用户 403；单块可被检索且兄弟块未重 embed；改正文 `diverged`、原件不变；删文档归档+删文件、记录行仍在；错误 profile 改块 422 |
| 4 | 工作区检索 API | `POST /api/kb/search` 必须带 `query` 与 `embedding_profile_id`；`retrieve(method=hybrid, top_n=4, rerank=False)`；filters：chunk + active + enabled + doc_* + profile | `method=auto`；UI；写稿 | A 入库 B 检索为空；停用块不可见；省略 profile 422 |
| 5 | 知识库工作区 UI | `matrix.html` 增加 kb 导航；顶栏当前模型只用于新建/检索/未保存预览；文档徽章只读；每行切片独立保存/停用/删除；预览可「入库此块」；换模型须确认重切 | 改 compose/reply 拓扑；跨 profile 融合检索；静默 PATCH 换模型 | 顶栏 B 不能写进 A 文档块请求；检索不能省略 profile；不必整篇保存才能入库 |
| 6 | 写稿 RetrieveKb | `retrieve_and_gate_draft` 在案例之后并列检索 KB；投影 `k1` / `[[kb:]]`；失败记 `kb_retrieve_failed` 不 skip；create/reply 带同一 profile（缺省 yaml default） | 混用 `[[ref:]]`；发送；多模态；因 kb 命中放宽功效空案例门 | 只引 kb 不能过 `missing_ref_on_empty_rag`；空库可写；案例失败语义不变 |

---

## 依赖与落点

| Step | 主要代码 / 配置 |
|---|---|
| 1 | RecordStore 工厂；`data/matrix/embedding_profiles.yaml`；`MATRIX_KB_RECORD_ROOT` |
| 2 | `integrated_agent/rag/`；`llama-index-core`、`pypdf`、`python-multipart` |
| 3 | `runtimes/matrix/rag/knowledge.py`；`transports/http/matrix/routes/kb_api.py`；`bootstrap/matrix_service.py` |
| 4 | 同上 kb HTTP `POST /api/kb/search` |
| 5 | `static/matrix.html`、知识库 JS/CSS |
| 6 | `runtimes/matrix/host/drafting.py` |

鉴权对齐收藏夹（Bearer / `X-User-Token`）。身份库只解析 `user_id`。

```text
Step1 骨架 → Step2 预览 → Step3 切片入库 → Step4 search → Step5 UI → Step6 写稿召回
```

---

## 本轮不做

多模态 / `.image()`、hierarchical Parser、Chroma auto、身份库兼知识库表、LlamaIndex Retriever / QueryEngine、渠道发送。
