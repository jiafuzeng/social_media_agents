# MatrixCopilot 知识库切分策略

用户私有知识库的文本切分。切分只负责把文档变成可预览、可入库的块；写稿召回仍走 Agently RecordStore，与案例夹具并列。

| 项 | 值 |
|---|---|
| 产品 | MatrixCopilot 知识库 |
| 依据 | [LlamaIndex Node Parser](https://developers.llamaindex.ai/python/framework/module_guides/loading/node_parsers/modules) |
| 代码位置（落地后） | `integrated_agent/rag/` |
| 文档日期 | 2026-08-18 |
| 状态 | **已拍板** |

配套：

- 产品总案：[MatrixCopilot-项目方案.md](./MatrixCopilot-项目方案.md)（§7 案例 RAG 与知识库并列，不互相替换）
- 工程评审：[MatrixCopilot-工程方案-技术评审.md](./MatrixCopilot-工程方案-技术评审.md)

已作废、不再使用的旧策略名：`chars` / `paragraphs` / `structure` / `semantic`（旧义）/ `unstructured`。不以别名兼容。

---

## 1. 统一概念

LlamaIndex 没有另一套名为 Chunking Strategy 的一等枚举。

| 概念 | 含义 |
|---|---|
| Document | 源文档容器（粘贴文本或上传文件） |
| Node | 源文档的一块，即 chunk；继承 Document 的 metadata |
| Node Parser | `Document[] → Node[]`，这就是分块 |

前端选的是 **哪一种 Node Parser**。后端只做：

```text
文本 → Document
    → 选定的 Node Parser（必要时再链 SentenceSplitter 控长）
    → Node[]
    → 投影成 TextChunk 给预览 / 以后 RecordStore.put
```

不用：`VectorStoreIndex`、QueryEngine、LlamaIndex DocStore、`LangchainNodeParser`、Chonkie `Chunker`。

---

## 2. 控长规则

`SentenceSplitter` / `TokenTextSplitter` / 链式里第二次切分：

- 参数名：`chunk_size`、`chunk_overlap`
- 单位：**token**（不是汉字个数）
- 默认：`chunk_size=512`，`chunk_overlap=64`（官方默认 1024/20；写稿召回改小，避免一块过大、向量被稀释）
- `chunk_overlap` ≥ 0 且必须小于 `chunk_size`

**没有自身 `chunk_size` 的 Parser，后端固定再跑一次 `SentenceSplitter`。** 用户不自己拼流水线。

中文断句：给 `SentenceSplitter` / `SemanticSplitterNodeParser` 注入 `。！？；\n` 等，不改官方算法。

单次最多 **80** 个 Node，超出则 422。

---

## 3. P0 策略（页面六张卡）

默认卡：`sentence`。

| `strategy` | Parser | 前端名称 | 行为 |
|---|---|---|---|
| `sentence` | `SentenceSplitter` | 按句子 | 尽量在句边界收口后再按 token 打包 |
| `token` | `TokenTextSplitter` | 按 Token | 按 token 硬切 |
| `markdown` | `MarkdownNodeParser` → `SentenceSplitter` | 按 Markdown 标题 | 按标题成节，节过长再切；块带标题路径 |
| `markdown_element` | `MarkdownElementNodeParser` → `SentenceSplitter` | 按 Markdown 元素 | 标题 / 正文 / 表 / 代码分成不同类型 Node |
| `semantic` | `SemanticSplitterNodeParser` → 超长再 `SentenceSplitter` | 按语义 | 相邻句 embedding 相似度骤降处切开，再用 token 上限兜底 |
| `sentence_window` | `SentenceWindowNodeParser` | 句子窗口 | 一句一个 Node；前后句写入 `metadata.window`，**不**参与 embedding |

### 3.1 各卡额外参数

| 策略 | 额外参数 | 默认 |
|---|---|---|
| `semantic` | `breakpoint_percentile_threshold`、`buffer_size` | 95、1 |
| `sentence_window` | `window_size` | 3（不调 `chunk_size`） |
| `markdown` | 标题深度 | 到三级 |

`semantic` 的 embedding 与以后 RecordStore 入库用同一套网关。失败则降级为 `sentence`，预览 `notes` 写明，页面仍出块。

### 3.2 `sentence_window` 入库约定

现在定死，避免以后改卡：

- 向量 / 关键词打在 **单句** `text` 上
- `window` 只放 metadata
- 写稿投影 offered 卡时，展示与引用用 **window**（对齐官方 `MetadataReplacementNodePostProcessor`）
- P0 预览同时列出句子与 window 两列

---

## 4. P1 策略（有场景再开）

| `strategy` | Parser | 何时开 |
|---|---|---|
| `unstructured_element` | `UnstructuredElementNodeParser` | 上传 PDF/DOCX，按 Title / 表格 / 列表切 |
| `html` | `HTMLNodeParser` | 有 HTML 知识库入口 |
| `hierarchical` | `HierarchicalNodeParser` | RecordStore 能存 parent 链接，且召回愿意做「子块命中则提父块」 |

### 4.1 为何 P0 不上 `hierarchical`

`HierarchicalNodeParser` 一次切出多层大小的 Node（如 2048 / 512 / 128），小块指向父块。官方价值在检索期的 AutoMergingRetriever：叶子命中够多就换成父块。

P0 召回是 RecordStore `hybrid` + `top_n=4`，没有自动提父。只切层级、检索仍当扁平块，同一段会以多种粒度重复入库。控长已有 `sentence` / `token`，手册结构已有 `markdown`。等召回能合并再做成卡，参数为 `chunk_sizes`。

---

## 5. 明确不做

| Parser | 原因 |
|---|---|
| `JSONNodeParser` | 知识库不是 JSON 库 |
| `CodeSplitter` | 知识库不是代码库 |
| `LangchainNodeParser` | 再引入一套分块栈 |
| Chonkie `Chunker` | 同上 |
| `SimpleFileNodeParser` | 按扩展名自动选 Parser，适合上传分流，不适合用户点一张卡 |
| `TopicNodeParser` / `SlideNodeParser` / `SemanticDoubleMergingSplitterNodeParser` | 过专 |
| `SimpleNodeParser` | 旧名，用 `SentenceSplitter` |

---

## 6. 块投影

所有 Parser 的 Node 收成同一种 `TextChunk`，预览与入库共用：

```text
text            ← node.get_content()
char_start/end  ← start_char_idx / end_char_idx
metadata        ← Document 继承字段 + 标题路径
element_type    ← markdown_element / 以后 unstructured 的类型
window          ← 仅 sentence_window
```

入库（后续文档）：`collection=kb`，`kind=chunk`，`indexed+vector`，`scope.user_id + doc_id`。过滤只靠 `scope.user_id`。标题路径可展示，不当检索硬过滤。

---

## 7. HTTP 与前端

登录态，协议对齐收藏夹（Bearer / `X-User-Token`）。

```text
GET  /api/kb/chunk-strategies
POST /api/kb/preview-chunks
     { text, strategy, chunk_size, chunk_overlap, … }
```

`strategy` 枚举仅限 §3 六个值。

前端：左侧导航知识库工作区。左栏选策略与参数，右栏展示后端返回的每一块（序号、长度、标题路径、正文；窗口策略另显示 window）。进入工作区后用示例 Markdown 自动预览一次。

---

## 8. 落地顺序

1. `llama-index-core` + `sentence` / `token` + 预览 API 与页面  
2. `markdown`、`markdown_element`  
3. `semantic`（embedding 可用后）  
4. `sentence_window`（预览两列；写稿扩窗可同批或紧随入库）  
5. P1：`unstructured_element` / `html` / `hierarchical`

---

## 9. 验收

- 六张卡都能选出，后端切分结果出现在右侧列表  
- `markdown` 块带标题路径  
- `semantic` 在 embedding 不可用时降级 `sentence` 且 `notes` 非空  
- `sentence_window` 预览同时有句子与 window  
- 未登录预览 401；非法 `strategy` 422；超过 80 块 422  
- 切分不写入 LlamaIndex 向量库；写稿检索不调用 LlamaIndex Retriever  
