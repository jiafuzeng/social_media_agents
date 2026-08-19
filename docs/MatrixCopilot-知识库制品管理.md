# MatrixCopilot 知识库制品管理

用户上传（或粘贴）的知识库制品如何增删改查，以及如何与 Agently RecordStore 对齐。切分见切分策略；写稿召回见项目方案 RetrieveKb。本文不写实现代码。

| 项 | 值 |
|---|---|
| 产品 | MatrixCopilot 知识库 |
| 框架 | `agently==4.1.4.4` RecordStore |
| 代码位置（落地后） | 门面 `runtimes/matrix/knowledge.py`；切分 `integrated_agent/rag/`；冷文件 `workspace/kb/files/`；RecordStore 目录 `workspace/kb/records/`（即 `records.db`，禁止再套 `.agently/records`） |
| 文档日期 | 2026-08-19 |
| 状态 | **已拍板** |

配套：

- 产品总案：[MatrixCopilot-项目方案.md](./MatrixCopilot-项目方案.md)
- 切分：[MatrixCopilot-知识库切分策略.md](./MatrixCopilot-知识库切分策略.md)
- RAG 计划表：[MatrixCopilot-RAG计划.md](./MatrixCopilot-RAG计划.md)
- 工程评审：[MatrixCopilot-工程方案-技术评审.md](./MatrixCopilot-工程方案-技术评审.md)

---

## 0. 拍板

用户改的是**一份文档聚合**。RecordStore 是这份聚合的检索后端，不是第二套可单独编辑的目录。所有写入口只走宿主门面 `KnowledgeStore`。

| # | 议题 | 决定 |
|---|---|---|
| A1 | 换文件 / 重切 | 覆盖同一产品 `doc_id`。旧 chunk 归档，新 chunk `put`。页面仍是一行。 |
| A2 | 切片独立 CRUD | **每一块可单独**增、改、停用、删并**当场入库**（embed + RecordStore `put`）。操作一块不重写、不重 embed 兄弟块。整篇重切只是便捷，不是唯一入库路径。 |
| A3 | 删除文档 | 先归档（检索不可见），再删冷文件。RecordStore **不硬删**记录行。 |
| A4 | 身份库 | 只解析 `user_id`。不建 `kb_documents` 表。 |
| A5 | 与案例 RAG | 分库、分 `collection`、分引用 token。功效门仍只认 `[[ref:]]`。 |
| A6 | RecordStore 落盘 | 目录固定 `workspace/kb/records/`（可用 env `MATRIX_KB_RECORD_ROOT` 覆盖）。库文件就是该目录下的 `records.db`。禁止框架默认的 `…/.agently/records/records.db`。 |
| A7 | 向量检索 | **必开**。chunk `put(..., indexed=True, vector=True)`；RetrieveKb `method="hybrid"`（向量 + 关键词）。embedding 未配置或调用失败 → 进程/入库 fail-closed，禁止 silently 变纯关键词。 |
| A8 | 多 embeddings Agent | 可配置多套、入库与检索都要**显式选一个** `embedding_profile_id`。查询向量只用该 Agent；召回只命中 `meta.embedding_profile_id` 相同的 chunk。禁止跨 profile 混 cosine / 融合。 |
| A9 | 前端防混用 | 工作区「当前模型」只约束**新建与检索**。已入库文档的改/删/分段/换文件锁定该文档的 profile。换模型必须走「重新索引」确认，禁止下拉框直接 PATCH。 |

---

## 1. 三件东西

```text
冷文件 artifact     原件字节、sha256、mime、路径
  ↑ meta.artifact_id
文档卡  document     标题、策略、启用、状态；产品主键 doc_id
  ↑ link contains
检索块  chunk        切分正文 / window；RetrieveKb 只搜这个
```

| 层 | 所有者 | 放什么 | 不放什么 |
|---|---|---|---|
| 冷文件 | 宿主目录 | 上传原件；可下载 | 向量、FTS、身份库 |
| 记录 | 独立 RecordStore root | `kind=document` 目录卡；`kind=chunk` 可检索块 | Flow 快照、案例夹具、用户密码 |
| 门面 | KnowledgeStore | 鉴权、403、事务顺序、HTTP 投影 | 再包一层假 RecordStore；让 Index 当真相 |

页面「一篇知识库文档」= document +（可选）artifact。写稿 `[[kb:k1]]` 只来自 **启用且未归档** 的 chunk。

切分仍只做 `Document → Node → TextChunk`。LlamaIndex Retriever / QueryEngine / VectorStoreIndex 不当库。

---

## 2. RecordStore 约束（4.1.4.4）

落地时必须按此适配，不要假设有通用 CRUD。

| 能力 | 实际 |
|---|---|
| `put` | 每次新 `record_id`，只增 |
| `get` / `get_data` / `search` / `retrieve` | 读 |
| `link` / `links` | 血缘 |
| 公开 `delete` 记录 | **无** |
| 改正文 | **无**。`put_record` 只改已有记录的 meta / summary / scope 等，不改 content |
| `put_artifact_ref` | Flow 运行产物：`collection=artifacts` + `scope.run_id`。**禁止**当用户上传库 |
| 向量 `delete_records` | 仅索引维护，不是业务删除 |

过滤是等式（或列表包含），支持 `collection`、`kind`、`scope.*`、`meta.*`。RetrieveKb 不能做「join 文档表再滤块」，必须把文档启用态**冗余到 chunk.meta**。

`LocalRecordStore.search` 会扫该 root 下全部 records 再过滤。因此知识库必须**独立 root**，不得与 identity、TriggerFlow checkpoint / snapshot / RuntimeEvent 混文件。

### 2.1 落盘路径（A6）

配置项：`MATRIX_KB_RECORD_ROOT`，默认相对仓库根目录 `workspace/kb/records`。相对路径相对项目根解析。

落盘约定：

```text
workspace/kb/records/records.db      # 记录 + FTS + 表 record_store_vectors（sqlite 向量）
workspace/kb/records/records.db-wal  # sqlite 附属，可出现
workspace/kb/files/{user_id}/…       # 冷文件，不是 RecordStore
```

**禁止**出现 `workspace/kb/records/.agently/records/records.db`。

原因：`RecordStore("workspace/kb/records")` 在首次访问 `.backend` 时，若未传入现成 backend / `provider`，会把物理根改成 `传入路径 / .agently / records`。这是框架给「普通项目目录」用的默认套娃，知识库不要走这条。

落地构造必须把 **LocalRecordStore 的 root 设成配置目录本身**，再交给 `RecordStore`，并在同一份 backend 上挂 **路由型** embedding 与 sqlite 向量（见 §2.2）。不要：`RecordStore(root)`、`agent.use_record_store(root)`。不要为了躲套娃去设一个会换掉整个 backend 的 `provider=`。

验收：进程起来后 `store.backend.db_path` 的父目录就是配置的 `MATRIX_KB_RECORD_ROOT`，路径中不得含 `.agently`；至少一套 embedding profile 可用，`vector_store_provider` 非空。

### 2.2 Embedding 与向量（A7 / A8）

RecordStore **不读** `DEEPSEEK_*`，也没有内置「填个模型名就出向量」。向量检索 = 两块独立缝：

| 缝 | 职责 | 本项目 |
|---|---|---|
| `embedding_provider` | `embed_texts(list[str]) → list[list[float]]` | 宿主 **路由**：按当前 `embedding_profile_id` 选 Agent；与写稿 chat 分配置 |
| `vector_store_provider` | 存向量、`search_by_embedding` | 钉死 `sqlite`，表在同一 `records.db` 的 `record_store_vectors` |

`vector_store_provider="auto"` 会先试 Chroma（目录变成 `records/vectors/chroma`），失败再 sqlite。路径要可控，**不要 auto**。

框架一份 RecordStore 只能挂 **一个** `embedding_provider`。多套模型不是多套 RecordStore 目录，而是门面里的 profile 表 + 一个路由 provider。`put(..., vector=True)` 与 `retrieve(method=hybrid)` 都会调这个 provider：入库前、检索前必须先绑定当前 profile，否则会用错空间。

#### Profile（可选的 embeddings Agent）

配置文件（落地）：`data/matrix/embedding_profiles.yaml`。密钥走 env，不写进仓库。

```yaml
default: openai-small
profiles:
  openai-small:
    label: OpenAI 3 small
    base_url: ${ENV.EMBEDDING_OPENAI_BASE_URL}
    api_key: ${ENV.EMBEDDING_OPENAI_API_KEY}
    model: text-embedding-3-small
  bge-m3:
    label: BGE-M3
    base_url: ${ENV.EMBEDDING_BGE_BASE_URL}
    api_key: ${ENV.EMBEDDING_BGE_API_KEY}
    model: bge-m3
```

`MATRIX_KB_EMBEDDING_DEFAULT` 可覆盖 yaml 的 `default`。列表为空或 default 指向不存在的 id → 进程起不来。

每个 `profile_id` 对应 **一个** embeddings Agent，禁止共用写稿 DeepSeek Agent：

```text
Agently.create_agent("matrix-kb-embed:{profile_id}")
  .set_settings("OpenAICompatible", {
      model_type: "embeddings",   # 只打 /embeddings
      base_url, model, api_key,
      stream: false,
  })
→ AgentEmbeddingProvider(agent)
```

只对该 agent `set_settings`，禁止 `Agently.set_settings` 全局改 `model_type`。DeepSeek chat **没有** embeddings；profile 必须是真正的 `/embeddings` 网关。

协议仍是 OpenAI 兼容：`POST {base_url}/embeddings`，`data[].embedding`。

#### 绑定关系（入库 ↔ 检索）

`embedding_profile_id` 是产品主键之一，和 `doc_id` 一样由宿主签发校验，**不是** RecordStore `id`。

| 动作 | 选谁 | 写/滤什么 |
|---|---|---|
| 入库 / 重切 / 改正文（新 chunk） | 请求里的 id，缺省则 `default` | document 与每条 chunk 的 `meta.embedding_profile_id`；embed 用该 Agent |
| 工作区 search / RetrieveKb | **必须带同一个 id**（请求或用户当前选择；缺省则 default，但仍要写入本次过滤） | `filters["meta.embedding_profile_id"]=该 id`；query embed 用该 Agent |

不变式：

1. 查询向量只来自所选 Agent。
2. 向量命中只来自 `meta.embedding_profile_id` 等于所选 id 的 **active** chunk。
3. 禁止一次 retrieve 打多个 profile 再拼分（空间不同，cosine 无意义）。
4. 文档用 A 入库、检索选 B → 该文档不出现（空命中，不 skip、不报错当成功召回）。
5. 改一篇文档的 profile = 按新 Agent **重切重入库**（旧 chunk 归档）。PATCH 只改标题/启用不得偷换 profile。
6. `semantic` 切分与该次入库共用所选 Agent 的 `embed_texts`。

路由 provider 用「当前绑定」而不是全局可变单例：门面在 `put`/`retrieve` 同步上下文里设置 `embedding_profile_id`（如 contextvar），路由再 `agents[id].embed_texts`。未知 id → 422。未绑定就 embed → 视为实现错误，fail-closed。

#### 为何不能写在 RecordStore(...) 关键字里

`RecordStore(LocalRecordStore(...), embedding_provider=...)` 会报错：现成 backend 不能再配 component。路由 provider 与 sqlite 向量必须 `configure_component_loaders` 挂在那份 LocalRecordStore 上。

```python
backend = LocalRecordStore(root, create=True, mode="read_write")
backend.configure_component_loaders(
    embedding_provider_loader=lambda: RoutingEmbeddingProvider(agents),
    vector_store_provider_loader=lambda: (
        SQLiteVectorStoreProvider(root / "records.db", create=True),
        "sqlite",
        None,
    ),
)
store = RecordStore(backend, mode="read_write")
```

`agents` 是 `profile_id → AgentEmbeddingProvider`。RecordStore 只看见一个 provider。

#### 检索调用

```text
绑定 embedding_profile_id
retrieve(
  query,
  method="hybrid",
  rerank=False,
  selection="top_n",
  top_n=4,
  scope={ user_id },
  filters={
    collection: kb, kind: chunk,
    meta.status: active, meta.enabled: true,
    meta.doc_status: ready, meta.doc_enabled: true,
    meta.embedding_profile_id: <绑定值>,
  },
)
```

不要 `method="auto"`。keyword 半边也会被同一套 filters 限制，因此不会搜到其它 profile 的块。

chunk：`indexed=True` 且 `vector=True`。embed 空或抛错 → 入库失败。document 仍 `vector=False`。

换 `model` / 维度：视为新 profile（新 id），不要在旧 id 上改语义。

---

## 3. 标识与记录形状

### 3.1 标识

| 标识 | 谁签发 | 可见范围 |
|---|---|---|
| `doc_id` | 宿主 | 页面、HTTP；写在 document 与 chunk 的 `scope.doc_id` |
| `chunk_id` | 宿主 | 分段页；`scope.chunk_id` |
| `artifact_id` | 宿主 | 冷文件目录名；`meta.artifact_id` |
| RecordStore `id` | 框架 `allocate` | 仅门面；不出页面、不出 Draft、不出 `[[kb:]]` |

跨用户用别人的 `doc_id` → 403。过滤隔离只靠 `scope.user_id`，标题路径不当硬过滤。

### 3.2 collection / kind

单一 `collection=kb`。

| kind | 角色 | indexed / vector |
|---|---|---|
| `document` | 目录卡 | 否（列表用 search + filter，不进写稿 retrieve） |
| `chunk` | 检索单元 | 是（`indexed` + `vector` 均 True；缺 embedding 则入库失败） |

禁止：把 PDF 字节 `put` 成 chunk；禁止案例卡写入本 collection。

### 3.3 document

`scope`：`user_id`、`doc_id`。

`meta`（现行目录以 meta 为准，因为 content 不可改）：

| 字段 | 含义 |
|---|---|
| `status` | `ingesting` \| `ready` \| `failed` \| `archived` |
| `enabled` | 用户开关；默认 true |
| `source` | `upload` \| `paste` |
| `filename` / `title` / `mime` | 展示 |
| `artifact_id` / `sha256` / `size_bytes` | 有原件才填 |
| `strategy` 及切分参数 | 与切分策略一致 |
| `chunk_count` | 当前 **active+enabled** 块数 |
| `embedding_profile_id` | 入库所用 embeddings Agent；改它必须重切 |
| `error` | `failed` 时原因 |

`put` 的 content 是**该次入库快照**（标题、当时策略、抽取正文摘要等），P0 不以 `get_data(document)` 当现行目录。现行字段读 meta。现行全文读冷文件或 active chunks。

### 3.4 chunk

`scope`：`user_id`、`doc_id`、`chunk_id`。

content：`TextChunk` 投影（`text`、`window`、`header_path`、`element_type`、`char_start`/`char_end`）。`sentence_window`：向量/关键词打在单句 `text`；写稿 quote 用 `window`。

`meta`：

| 字段 | 含义 |
|---|---|
| `status` | `active` \| `archived` |
| `enabled` | 用户停用该块 |
| `doc_status` / `doc_enabled` | 从文档冗余，供 retrieve 等式过滤 |
| `embedding_profile_id` | 与文档一致；RetrieveKb 等式过滤 |
| `artifact_id` | 产生该块时的原件；改正文后可仍指向旧件 |
| `diverged` | 改正文后为 true |
| `ordinal` | 展示序 |

### 3.5 链接

| relation | 方向 | 何时 |
|---|---|---|
| `contains` | document → chunk | 入库、增块 |
| `replaces` | 新 chunk → 旧 chunk | 改正文 |
| `supersedes` | 新 chunk 集合同一 `doc_id` 下的旧 active 块 | 换文件 / 重切（P0 可只靠 meta.status，链接作审计） |

P0 列表与召回**不**靠 walk link 当权威；权威是 `meta.status` + `meta.enabled` + `doc_*` 冗余。link 用于分段页「从哪来」和排错。

---

## 4. 冷文件

路径：`workspace/kb/files/{user_id}/{artifact_id}/{safe_filename}`。

- 粘贴入库可以没有 artifact。
- 不复用问数 `storage/artifacts.py`（无用户隔离，语义是生成物）。
- 不用 TaskWorkspace（任务文件空间，不是用户私有库）。
- 不用 `RecordStore.put_artifact_ref`。
- 大文件只冷存；记录留 `artifact_id` + sha256。多模态原图同一规则，后做。

下载：鉴权通过后按 document.meta.artifact_id 读自己的文件，禁止拼别人的路径。

---

## 5. CRUD 事务

门面是事务员。失败要让**检索侧**先不可见或从未可见；磁盘残留可扫，可检索幽灵块不行。

### 5.1 增（整篇便捷路径）

上传或粘贴可一次切完并循环单块入库（语义同 §5.5）。也可先建空文档再按块保存。

批量：绑定 `embedding_profile_id` → 冷文件 → `put` document（`ingesting`）→ Parser → **每块独立** `put`（一块失败只失败该块，已成功的保持 `active`）→ document `ready`，`chunk_count` = 成功块数。不要因第 N 块失败把前 N-1 块全部归档。document 创建本身失败才标 `failed`。

预览 API 仍无状态。预览里「入库此块」走 §5.5。

### 5.2 查

| 用途 | 怎么读 |
|---|---|
| 文档列表 / 详情 | `search`/`get`：`collection=kb`，`kind=document`，`scope.user_id`，排除 `meta.status=archived` |
| 分段列表 | 同 `doc_id` 的 chunk；默认只展示 `status=active`（可加筛选看停用） |
| 原件 | 冷文件 |
| 工作区检索预览 | 绑定所选 `embedding_profile_id` 后 `retrieve`，过滤同 RetrieveKb |
| 写稿 RetrieveKb | 只 chunk：active + enabled + doc ready/enabled + **`meta.embedding_profile_id` = 本次绑定** |

空库、零命中：可写稿，不 skip，不放宽禁词。知识库失败记 `kb_retrieve_failed` limitation。

### 5.3 改

| 用户动作 | 冷文件 | RecordStore |
|---|---|---|
| 改标题 | 不动 | document `put_record` 改 meta |
| 启用 / 停用整篇 | 不动 | document.meta.enabled；**所有**该 `doc_id` 且 `status=active` 的 chunk 回写 `doc_enabled` |
| 停用 / 启用某一块 | 不动 | 该 chunk `meta.enabled`；更新 document.`chunk_count` |
| 改正文 | 不动 | 绑定**文档已有** profile 后新 `put` chunk；不得换空间 |
| 换文件或重切 | 新 `artifact_id`（或覆盖后新 sha256） | 默认沿用文档 profile；请求若带**不同** profile 则整篇按新 Agent 重切（旧 chunk 归档） |
| 只改 embedding profile | 不动 | 不允许只 PATCH id；必须走重切 |

换文件不换 `doc_id`。进行中 document=`ingesting`，完成前 RetrieveKb 仍只看见尚未归档且 `doc_status=ready` 的块；建议先归档旧块再切新块，避免新旧同时可检索。

### 5.4 删

1. document `status=archived`，`enabled=false`
2. 该 `doc_id` 全部 chunk `status=archived`，向量 `delete_records`
3. 删除该文档名下冷文件目录

列表与 RetrieveKb 立刻不可见。记录行保留作审计。删单块见 §5.5，不删冷文件。

恢复不在 P0（归档即产品删除）。

### 5.5 切片独立入库（A2）

切片是可检索的最小持久化单元。**保存一块 = 这一块入库**，不是「改完所有块再点一次文档保存」。

一次单块事务（只动这一块）：

```text
校验 document 存在且未 archived；绑定 document.embedding_profile_id（忽略顶栏）
→ 该块 embed_texts([本块 text 或 window 规则下的索引文本])
→ put kind=chunk, indexed+vector, meta 继承文档 profile / doc_status / doc_enabled
→ link contains（新增）或 replaces（改正文）
→ 改正文/删除时：旧 record 归档 + delete_records([旧id])
→ 更新 document.chunk_count
```

| 单块动作 | 入库 | 兄弟块 |
|---|---|---|
| 新增（预览采纳、手写、从某一 Node 保存） | 本块 `put` + 向量 | 不动 |
| 改正文后保存 | 新 `put` + 新向量；旧块归档 | 不动 |
| 停用 / 启用 | 只 `put_record` enabled；向量保留 | 不动 |
| 删除 | 本块归档 + 清向量 | 不动 |

失败：该请求 4xx/5xx，本块不标 `active`（改正文则旧块仍 active，避免正文和检索一起丢）。其它块的检索不受影响。document 不因此变 `failed`。

embedding：永远用**文档** profile，单块不能带另一个 `embedding_profile_id`。索引文本规则与整篇相同（`sentence_window` 仍打单句 `text`）。

前端：分段表每一行独立「保存 / 停用 / 删除」。预览列表每一块可「入库此块」。没有「必须先保存全文」。整篇重切仍可用，会归档该文档全部旧块后再按新切分逐块入库（每一块仍是独立 `put`）。

---

## 6. HTTP

登录态，鉴权对齐收藏夹（Bearer / `X-User-Token`）。身份库只换 `user_id`。

```text
GET    /api/kb/embedding-profiles      # 可选 Agent 列表 + default
GET    /api/kb/chunk-strategies
POST   /api/kb/preview-chunks          # 不入库；semantic 可带 embedding_profile_id
POST   /api/kb/extract                 # 抽文本，不入库
POST   /api/kb/search                  # { query, embedding_profile_id } 绑定后 retrieve
POST   /api/kb/documents               # 可带 embedding_profile_id
GET    /api/kb/documents
GET    /api/kb/documents/{doc_id}
PATCH  /api/kb/documents/{doc_id}      # 标题、enabled、换文件、重切（换 profile 只能随重切）
DELETE /api/kb/documents/{doc_id}
GET    /api/kb/documents/{doc_id}/file
GET    /api/kb/documents/{doc_id}/chunks
POST   /api/kb/documents/{doc_id}/chunks          # 单块入库（正文 + 可选 ordinal）
PATCH  /api/kb/documents/{doc_id}/chunks/{chunk_id}  # 保存正文 / enabled；当场 embed
DELETE /api/kb/documents/{doc_id}/chunks/{chunk_id}  # 单块归档
```

`strategy` 仅切分策略六个值。别人的 `doc_id` → 403；未登录 → 401。未知或已下线的 `embedding_profile_id` → 422。

### 6.1 前端 CRUD：禁止混用 embedding（A9）

目标：用户在页面上怎么点，都不会把 **A 模型入库的块**拿去用 **B 模型**检索，也不会在改一篇已有文档时把 workspace 里刚选的模型悄悄写进请求。

#### 两层选择，不要合成一个下拉框

| 层 | 控件 | 管什么 | 不管什么 |
|---|---|---|---|
| 工作区当前模型 | 知识库顶栏（及写稿同一 session 键） | **新建**文档、**检索预览**、`semantic` 未入库预览、写稿 RetrieveKb | 已打开文档的分段改删 |
| 文档锁定模型 | 文档行/详情上的只读徽章 | 该 `doc_id` 的换文件、重切（不换模型）、增/改/停用分段 | 不能当「换模型」开关 |

`sessionStorage` 键建议：`matrix.kb.embedding_profile_id`。进入工作区：`GET /api/kb/embedding-profiles`，当前值 ∉ 列表则回退 `default` 并提示。顶栏切换只改这个键，**不**调文档 PATCH。

#### 列表与检索

- 每行展示入库模型（label + id）。无徽章不准上线。
- 检索请求**必须**带顶栏当前 `embedding_profile_id`，禁止省略靠后端 default（避免和页面显示不一致）。
- 默认列表可筛「当前模型」；全部文档要一眼能看出哪些检索不到。
- 零命中文案必须区分：库空 / 当前模型下无文档 / 有文档但 query 未中。若存在其它模型的文档，提示数量，并提供「切换到该模型」而不是「扩大检索融合」。

#### 打开已有文档后的 CRUD

详情一旦加载，后续请求的 `embedding_profile_id` **一律用 `document.embedding_profile_id`**，即使顶栏已经改成别的模型。

| 操作 | 前端发送 | 禁止 |
|---|---|---|
| 改标题、启用/停用篇或块 | 不带新 profile | 把顶栏 id 写进 PATCH |
| 改正文、增块、删块 | 每块单独请求；不换 profile；新块继承文档 id | 攒完全文再一次保存；用顶栏模型 embed 这一块 |
| 换文件 / 重切（同模型） | 显式带**文档**的 id | 带顶栏 id |
| 换模型重新索引 | 独立按钮 → 确认「旧向量作废，用 X 整篇重切」→ 重切 API 带**新** id | 徽章改成可编辑 select 后静默保存 |

顶栏与文档徽章不一致时：详情区提示「检索正使用 Y，本文档以 X 入库，当前检索看不到本文」。不要自动改顶栏（避免误伤正在进行的检索）；可提供「把检索切到 X」。

#### 新建

上传/粘贴：只用顶栏当前模型建文档。预览后**可以只入库选中的块**（每块一次 POST），不必一次提交全部 Node。预览与入库同一 `embedding_profile_id`；改顶栏则丢弃未保存预览并重跑。

分段表每一行：保存 / 停用 / 删除，各自独立请求（§5.5）。

#### 写稿

创作/回复与知识库共用 session 键，随任务 POST 带同一 `embedding_profile_id`。页面上只读展示「将按 X 检索手册」，不要在任务表单再放第二套模型选择。

#### 前端验收反例（必须挡住）

- 顶栏选 B 后给 A 文档改正文，请求里带了 B
- 检索省略 `embedding_profile_id`
- 换模型无需确认即 PATCH 成功
- 检索空结果不说明「模型不一致」
- 写稿与工作区顶栏模型不同

后端仍 fail-closed（未知 id 422、retrieve 等式过滤）。前端是防呆，不是第二套权限。

---

## 7. 与写稿的边界

- RetrieveKb 与 RetrieveCases 并列，不得并进同一节点。
- 签发本项 `k1`…，协议 `[[kb:k1]]`，不得占用 `[[ref:]]`。
- Gate：未知 `[[kb:]]` fail-closed；只引 kb **不能**满足 `missing_ref_on_empty_rag`。
- `/api/create`、`/api/reply` 须登录并校验会话归属；`user_id` 只给检索。
- 写稿 RetrieveKb 使用请求中的 `embedding_profile_id`（缺省 default）；与工作区当前选择绑定同一套 Agent，不得另开一套。

---

## 8. 明确不做

- 身份库 ORM 兼知识库表
- 问数 `ArtifactStore`、TaskWorkspace、`put_artifact_ref` 当用户库
- LlamaIndex Retriever / QueryEngine 当真相源
- `agently.integrations.chromadb` 当知识库 API（ANN 只走 RecordStore `vector_store_provider`）
- `RecordStore(路径)` / `use_record_store(路径)` 让库落到 `…/.agently/records/`
- 对 `records` 表 `DELETE` 当业务删除
- 页面或脚本绕过门面直接 `put`
- 知识库块写入案例 JSON
- RecordStore 官方示例的 `[[ref:]]` 与案例卡混用
- 无 embedding profile 仍启动、或 `method="auto"` 悄悄变成纯关键词
- 必须先保存全文、切片不能单独入库
- 一次 retrieve 融合多个 `embedding_profile_id`
- 用写稿 DeepSeek Agent 当 embeddings
- P0 文档版本树、恢复归档、跨用户分享
- P0 hierarchical / AutoMerging 检索

---

## 9. 验收

- 上传 / 粘贴后列表可见；`retrieve` 能命中新 chunk
- 停用文档或块后，写稿 retrieve 不再命中
- 换文件：同一 `doc_id`，旧正文不可检索，新正文可检索
- 改正文：`diverged=true`，原件下载仍是旧文件；只保存一块时兄弟块向量不变
- 单块入库失败不影响其它已入库块的 retrieve
- 删除：列表 404/空、retrieve 无命中、冷文件不在；records.db 仍有归档行
- 跨用户 403
- 缺 profile 表或 default 无效时 KnowledgeStore 起不来；入库 `vector=True` 失败则 document=`failed`
- 用 A 入库、用 B 检索：A 的块不得出现在 B 的命中里
- 打开 A 文档后改分段，即使顶栏是 B，请求仍绑定 A
- 只引 `[[kb:]]` 不能过功效空案例门
- 预览超过 80 块 422；入库超过 2000 块 422；预览不写 RecordStore
