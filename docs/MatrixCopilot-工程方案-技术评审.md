# MatrixCopilot 工程方案（技术评审）

| 项 | 值 |
|---|---|
| 评审对象 | P0 工程落地，不含发送 |
| 产品方案 | [MatrixCopilot-项目方案.md](./MatrixCopilot-项目方案.md) |
| 参照实现 | `integrated_agent/runtimes/question/` |
| 框架 | `agently==4.1.4.4`（与现仓库锁定一致） |
| 评审日期 | 2026-08-15 |
| 建议结论 | 原则通过，按本文契约实施；下列「待拍板」项当场确认 |

本文给技术评审用：拍板接口、状态机、失败策略和验收反例。产品叙事以项目方案为准，不在此重复。

---

## 0. 评审要拍的决定

| # | 议题 | 建议 | 备选 | 请评审确认 |
|---|---|---|---|---|
| D1 | 是否新增 Runtime `matrix` | 是。有独立任务生命周期、队列和稳定拓扑 | 挂到 Agent Action |  |
| D2 | 双 API 是否共用一套 Flow | 否。产品方案已拍板：`COMPOSE_FLOW` 与 `REPLY_FLOW` 分图。共享 TaskService / Gate / RetrieveCases | 一张图 + `scenario` 开关 | 已定 |
| D3 | P0 是否上向量检索 | 否。`RetrieveCases` 节点在，实现可返回 empty 或关键词过滤夹具 | P0 上 BM25+向量 |  |
| D4 | 降级是否允许 Review 回抬 | 否。skip/template 不可被 Review 改成可发正文 | Review 可推翻 Gate |  |
| D5 | 问数 stores 是否先抽取 | 否。先复制，Result 同构再抽 | 立刻抽通用 TaskService |  |
| D6 | Gateway auto 是否加入 matrix | 是。附件仍钉 agent，codex 仍显式 | 仅 `/agent matrix` |  |

否决项（写入纪要即可，不讨论翻案）：代码执行双引擎、四层事后约束、用 RAG 放行、P0 发送、关键词判断该不该回。

---

## 1. 工程目标与完成定义

**P0 完成定义：** 一次受理最终进入 `completed | partial | failed`；产出草稿包；每条草稿有 `decision`、`rationale`、`degrade_op`；硬规则触线被降级；未知 key fail-closed；不调用任何渠道发送 API。

**非目标：** HITL、真实平台抓取、向量索引、CRM、群发、独立扩缩容进程。

---

## 2. 模块与依赖

```text
bootstrap → transports / gateway / runtimes / storage
transports → gateway contracts + matrix models
gateway → AgentRuntime.stream
runtimes/matrix → gateway contracts
runtimes/matrix/analysis → 不依赖 HTTP、企业微信、Gateway、question.analysis
```

| 路径 | 所有者 | 不拥有 |
|---|---|---|
| `runtimes/matrix/models.py` | 入站与 TaskResult 契约 | 平台原始 frame |
| `runtimes/matrix/service.py` | 队列、503、任务状态 | 语义拆解 |
| `runtimes/matrix/worker.py` | 外层 analyze → publish SSE | Prompt |
| `runtimes/matrix/client.py` | SSE → GatewayEvent | 业务阶段 |
| `analysis/snapshots.py` | 短 key 投影、snapshot_id | 意图路由 |
| `analysis/constraints.py` | AC、字数、引用、降级算子 | 该不该回 |
| `analysis/retrieval.py` | RetrieveCases 契约 | 硬词表、放行 |
| `analysis/workflows/compose_flow.py` | 创作 TriggerFlow 与 close | 回复节点 |
| `analysis/workflows/reply_flow.py` | 回复 TriggerFlow 与 close | 趋势节点 |
| `chunks/compose|reply/{brief,draft,review}.py` | 各三条可观察阶段 | 发送 |
| `transports/http/matrix_api.py` | HTTP Transport → GatewayRequest | 直连队列 |
| `data/matrix/` | 演示快照与案例夹具 | 运行时状态 |

改动面（现有文件，评审需同意）：

| 文件 | 改动 | 回归 |
|---|---|---|
| `gateway/service.py` | `AUTO_RUNTIMES` 增加 matrix | `tests/test_gateway.py` |
| `gateway/intent.py` | instruct 增加写帖/评理 | 同上 |
| `bootstrap/im_assistant.py` | 注册 `MatrixServiceRuntime` | 启动组装测试 |
| `bootstrap/question_service.py` | `create_production_app` include matrix 路由 | 问数 `/v1/tasks` 行为不变 |
| `ARCHITECTURE.md` / `README.md` | 补第四 Runtime | 文档评审 |

---

## 3. 类型契约（建议直接落 Pydantic）

全部 `extra=forbid`。下列为评审锁定的字段，实现不得擅自增减语义。

### 3.1 入站

```text
MatrixTaskCreate
  text: str, min_length=1
  scenario: compose | reply              # 禁止 auto；由入口写入或调用方显式传
  platform_keys: list[str] = []          # 空则用账号默认平台
  account_key: str = "default"
  brand_key: str = "default"
  need_trends: bool = false              # 仅 compose；reply 忽略
  thread_key: str | null = null          # compose 携带 → 422
  comments: list[CommentIn] | null = null
  requester: str = "course-user"
  channel: str = "web"

CommentIn
  comment_key: str                       # 若缺，宿主签发 c1..cn
  text: str
  role: root | reply = root
  author_display: str | null = null      # 不得含 UID
```

HTTP Transport 把路径写成 `GatewayRequest`（`runtime_key` + `scenario`）后交给 `AgentGateway`，不直连 TaskService。`/api/create` → matrix + compose；`/api/reply` → matrix + reply（须有 `thread_key` 或 `comments`）；`/v1/matrix/tasks` 须显式 `scenario=compose|reply`。

企业微信：`text` 原样进入 Gateway；匹配 `thread:<key>` 则 `scenario=reply`，否则落到 matrix 时为 compose。附件不进 matrix。Brief 不做场景分类。

### 3.2 快照（execution resources）

```text
Snapshot
  snapshot_id: str                       # 内容哈希前 16
  account: {account_key, display_name, voice_summary}
  brand: {brand_key, forbidden_topics[], template_keys[]}
  platforms: [{platform_key, max_chars, mention_rules}]
  policy: {term_list_id, ac_ready: bool}
  comments: [{comment_key, text, role, author_display}]
  templates: [{template_key, text, claim_types[]}]
  trend_cards: [{trend_key, title, summary}]   # 可空
```

缺 brand 或 policy → 任务 failed，不调用模型。

### 3.3 Brief 输出

```text
ComposeBriefOut / ReplyBriefOut
  normalized_brief: str
  requirements: [{requirement_id, description}]
  work_items: [WorkItem]                 # 无 scenario；场景由 Flow 决定

WorkItem
  work_item_id: str
  kind: compose_post | reply_comment     # 必须与所在 Flow 一致
  requirement_ids: [str]
  platform_key: str
  source_comment_key: str | null
  goal: str
  talking_points: [str]
  claim_types: [str]                     # 必须 ⊆ offered
```

宿主：id 唯一；每个 requirement 被引用；`platform_key` / `source_comment_key` / `claim_types` ∈ offered。

### 3.4 Draft 输出（每项）

```text
DraftModelOut
  work_item_id: str                      # 不得被模型改掉
  stance_assessment: str                 # 有界，same-response
  reply_decision: reply | acknowledge | skip | null
  claim_types: [str]
  risk_flags: [str]
  draft_text: str
  rationale: str
  evidence_ids: [str]
  proposed_degrade: pass | rewrite_safe | template_fallback | skip | null
```

ComposeDraft 不得输出 `reply_decision`。ReplyDraft 必填 `reply_decision`。`skip` ⇒ `draft_text` 为空串。两套 schema 分文件，不靠 null 兼用。

### 3.5 Gate 结果

```text
GatedDraft
  draft_key: str                         # 宿主签发 d-<work_item_id>
  degrade_op: pass | rewrite_safe | template_fallback | skip
  degrade_trace: [{op, issues[], attempt}]
  text: str
  rationale: str
  decision: reply | acknowledge | skip | publishable
  evidence_ids: [str]
  status: ready | degraded | skipped | failed
  issues: [str]
```

`publishable` 仅表示「可进入 Review」，不是已发送。

### 3.6 Review 与 TaskResult

```text
ReviewOut
  item_verdicts: [{draft_key, verdict: accept | revise | reject, revised_text?, notes}]
  package_summary: str
  limitations: [str]

MatrixTaskResult
  task_id, snapshot_id, trace_ref
  status: completed | partial
  task_type: compose_post | reply_comment   # 由 Flow 决定，禁止 mixed
  summary: str
  drafts: [GatedDraft]                   # Review 后终态
  evidence: [{ref_id, title, ruling}]
  limitations: [str]
```

任务级 `failed` 仅当：快照缺失、Brief 无法解析、或全部 work_item `failed`。部分项 skip/degraded → `partial`（若至少一条 `ready`）或 `completed`（全部 ready）。建议锁定：

- 全部 ready → `completed`
- 存在 ready 或 degraded，且存在 skipped/failed → `partial`
- 零 ready 且零 degraded → `failed`

---

## 4. TriggerFlow 与状态

```text
COMPOSE_FLOW
  [fetch_trends]
  → compose_brief
  → for_each(retrieve_and_compose_draft, concurrency=4)
  → compose_review

REPLY_FLOW
  reply_brief
  → for_each(retrieve_and_reply_draft, concurrency=4)
  → reply_review
```

`retrieve_and_*_draft` 是 Chunk，内部顺序：RetrieveCases → 对应 Draft ModelRequest → ConstraintGate（必要时 rewrite_safe 一次）→ 返回 GatedDraft。  
不把 Gate 画成模型节点。不把 retrieve 并进 Brief。不用 `when(scenario)` 把两张图焊回一张。

P0 `need_trends=true`：仅 `COMPOSE_FLOW` 前奏用夹具或空列表；失败记 limitation，不失败整单。`REPLY_FLOW` 无此节点。

### 4.1 execution state

| key | 写入 | 读取 |
|---|---|---|
| request | run 前奏 | 全程 |
| snapshot | 前奏 | brief / draft / review |
| brief | brief chunk | retrieve、draft、review |
| drafts | append GatedDraft | review、worker |
| package | review | worker → TaskResult |
| final_failed | review 或 brief 失败 | run 收口 |

### 4.2 ConstraintGate 算法（宿主，必须单测）

```text
issues = []
if reply_decision == skip and text.strip(): issues += ["skip_must_be_empty"]
if reply_decision != skip and kind == reply and not text.strip(): issues += ["empty_reply"]
if len(text) > platform.max_chars: issues += ["over_limit"]
if AC.match(text): issues += ["forbidden_term:"+term]
if any(id not in offered_refs): issues += ["unknown_ref"]
if requires_citation(claim_types) and not evidence_ids:
    if retrieval_state == empty: issues += ["missing_ref_on_empty_rag"]
if proposed_degrade not in {None} ∪ allowed_ops: ignore proposal

if not issues: return pass
if only over_limit or soft claim and attempt==1: rewrite_safe
elif hard term or missing_ref_on_empty_rag: template_fallback if template_key offered else skip
elif skip_must_be_empty: force text="" and skip
else: skip
```

`requires_citation`：`claim_types` 与 `{guaranteed_return, efficacy, medical, crypto_promotion, superlative}` 相交则为真。该集合写在 `constraints.py` 常量，不写进模型 prompt 当唯一来源（prompt 里给可读说明，执行以常量为准）。

### 4.3 RetrieveCases（P0）

```text
RetrieveQuery
  work_item_id, platform_key, claim_types[], goal

RetrieveResult
  state: hits | empty | failed
  cards: [{ref_id, title, ruling, allowed, forbidden, quote, claim_types[]}]
```

P0 实现：对 `data/matrix/cases/*.json` 做 `platform_key` + `claim_types` 精确过滤，最多 4 张。零命中 → `empty`，不是 `failed`。文件读失败 → `failed`，该项降级 skip，不崩整单。

P1 才替换为 BM25/向量；接口不变。

---

## 5. HTTP / SSE / Gateway

### 5.1 路由

| 方法 | 路径 | 行为 |
|---|---|---|
| POST | `/api/create` | HTTP Transport → Gateway，`runtime=matrix` `scenario=compose` |
| POST | `/api/reply` | 同上，`scenario=reply`；缺评论则 422 |
| POST | `/v1/matrix/tasks` | 同上；`scenario` 非 compose/reply 则 422 |
| POST | `/v1/tasks` | HTTP Transport → Gateway，`runtime=question` |
| GET | `/v1/matrix/tasks/{id}` | TaskSnapshot |
| GET | `/v1/matrix/tasks/{id}/events` | SSE，协议对齐问数 |

公开 POST 一律经 `AgentGateway`。TaskService 的受理与 SSE 仅供 Runtime 内部调用。matrix 使用独立 `MatrixTaskService` 实例与独立队列。

### 5.2 SSE 事件（稳定名，禁止暴露 TriggerFlow 私有对象）

| event_type | data | Gateway 映射 |
|---|---|---|
| task.submitted | requester, channel | run.created |
| worker.started | worker_index | status.update |
| stage.started/completed | stage | status.update |
| work_item.ready | work_item_id, kind | status.update |
| draft.ready | draft_key, decision, degrade_op | evidence.ready |
| package.ready | summary, draft_count | message.delta（一次，正文=summary） |
| task.completed | status, snapshot_id, trace_ref | run.completed |
| task.failed | error_type, message | run.failed |

`package.ready` 只发布一次。重放 SSE 不得重复映射成多段 delta。

### 5.3 Gateway

```text
AUTO_RUNTIMES +=
  matrix: "写推文、多平台草稿、回复评论与评理"
```

`/agent` 用法增加 `matrix`。intent instruct：写帖、回评、社媒口径 → matrix；经营指标 → question；其余 → agent。

---

## 6. 数据文件（P0 最小集）

| 文件 | 最低内容 |
|---|---|
| `data/matrix/accounts.yaml` | `default` 账号 + voice_summary |
| `data/matrix/platforms.yaml` | `x-twitter`：`max_chars=280` |
| `data/matrix/policy_terms.yaml` | 演示禁词（如「稳赚」「治愈」） |
| `data/matrix/templates.yaml` | 1 条中性免责模板 |
| `data/matrix/sample_threads.json` | `demo-1`：3 评，含 1 条攻击 |
| `data/matrix/cases/x-twitter.json` | 已有 12 条夹具 |

`snapshot_id` = 上述文件规范化 JSON 的 SHA-256 前 16 位。任一文件变更即新快照。

---

## 7. 测试矩阵（评审验收表）

| ID | 类型 | 断言 |
|---|---|---|
| T01 | unit | 未知 `platform_key` → 创建失败或 snapshot fail |
| T02 | unit | AC 命中「治愈」→ degrade ≠ pass |
| T03 | unit | skip + 非空正文 → 强制空 + skip |
| T04 | unit | 未知 `evidence_id` → 该项 failed/skip |
| T05 | unit | empty RAG + efficacy claim → 不得 pass |
| T06 | unit | Brief 漏 requirement → 校验失败 |
| T07 | flow | 两平台 compose → 2 条 draft（脚本化模型） |
| T08 | flow | 三评论含攻击 → ≥1 skip 且 text=="" |
| T09 | flow | 一项 Gate 失败 → 任务 partial 且成功项仍在 |
| T10 | flow | Review 试图抬回 skip → 宿主拒绝，保持 skip |
| T11 | http | POST create → 202；events 含一次 package.ready |
| T12 | http | 队列满 → 503 + Retry-After |
| T13 | http | 问数 `/v1/tasks` 回归仍 202 |
| T14 | gw | auto offered 含 matrix，不含 codex |
| T15 | gw | 附件 → agent，不进 matrix |

S4 真模型（不阻塞合入）：X 预热一题；`thread:demo-1` 一题。看 `logs/<task_id>/run.json`，不用正则判正文质量。

---

## 8. 观测与 Trace

对齐问数 `TraceLog` 形态，业务事件 allowlist：

- `business.matrix.snapshot_bound`
- `business.matrix.briefed`
- `business.matrix.retrieved`（state=hits|empty|failed, card_count）
- `business.matrix.drafted`
- `business.matrix.gated`（degrade_op, issues）
- `business.matrix.reviewed`
- `business.matrix.packaged`

禁止写入完整 Prompt、密钥、评论作者 UID。`run.json` 落 `logs/<task_id>/`。

---

## 9. 已否决方案（评审备忘）

| 方案 | 否决原因 |
|---|---|
| 指令性路径 + 代码执行 | 社媒草稿无代码对象，引入不可逆副作用 |
| 四层事后约束 | 变成生成后过滤；RAG 校验会漏拦 |
| 检索基座喂给约束层 | 违禁词必须完备扫描 |
| Skill 映射意图 | 抢模型语义所有权 |
| 爆款/人设与路径一一绑定 | 人设两条都要；爆款只进创作 Flow |
| 一套 Flow + Brief 做 scenario/auto 分类 | 产品方案已拆开；入口绑定拓扑 |
| 通用 TaskService 先抽象 | 没有第二消费者证明 |
| instant 发送 | 草稿是临时值 |

---

## 10. 风险与开放问题

| 级别 | 风险 | 缓解 | 是否阻塞 P0 |
|---|---|---|---|
| 高 | 夹具被当成现行法 | disclaimer + 生产改人工摘录 | 否，文档写明即可 |
| 高 | Review 与 Gate 冲突 | 本文 D4：不可回抬 | 否，写成单测 T10 |
| 中 | auto 误路由 | 卡片文案 + `/agent matrix` | 否 |
| 中 | 复制队列壳分叉 | 只复制 service/stores | 否 |
| 中 | 中文分词导致 AC 漏拦 | P0 用整词/子串匹配；列已知限制 | 否 |
| 低 | Gateway 无结构化评论 | `thread:demo-1` | 否 |

开放问题（请评审口头定）：

1. `max_chars`：X 按字符还是按加权长度？P0 建议按 Python `len(text)`，文档标明。
2. 是否保留 `escalate` 枚举？否。不露出；若模型写出则 Gate 映射为 skip。全程无人工审批。
3. 矩阵默认平台列表：仅 `x-twitter`，还是 P0 就加 `weibo` 空壳？建议只锁 X，第二平台 P1。

---

## 11. 建议评审结论稿

> 原则通过 MatrixCopilot P0 工程方案。新增 `matrix` Runtime，双 API 绑定两套 TriggerFlow（`COMPOSE_FLOW` / `REPLY_FLOW`），共享 TaskService、ConstraintGate 与 RetrieveCases。约束层为注入 + Gate，RAG 仅作各自 Brief 之后的证据节点。P0 不发送、不上向量检索、Brief 不做场景分类。合入以 T01–T15 全绿为门禁；S4 真模型不阻塞。D2 按产品方案已定执行，其余 D1/D3–D6 按建议执行。开放问题 1–3 按上文建议默认。

评审记录：日期 / 参与人 / 异议 / 后续 action，会后补进本节。
