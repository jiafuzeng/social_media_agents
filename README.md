# 综合智能体集成工程

这是整门课程的总结性工程。它说明不同执行模型如何通过同一个 Gateway 被客户端安全、稳定地使用，以及这些模型组合后需要处理哪些系统边界。

本目录是第 26 课提供的优化工程方案。业务需求和验收目标来自第 25 课，目录和模块责任经过重新梳理，依赖固定为 `agently==4.1.4.4`。工程使用的核心接口在 4.1.4.2—4.1.4.4 之间保持兼容；资料包固定具体版本，方便学员复现同一套结果。

## 解决的核心问题

- **统一入口**：企业微信请求先归一为 `GatewayRequest`，再由模型意图或明确指令选择运行时。
- **统一事件**：Agently、问数 SSE 和 ACP 输出都转换为 `GatewayEvent`。
- **通用 Agent**：搜索、Browse、Skills、Actions、Workspace、Python 和 Shell 沙盒属于同一个 Agently Agent 运行时。
- **流程 Agent**：问数使用固定 TriggerFlow 流程，并作为可独立扩缩容的 HTTP/SSE 服务运行。
- **外部 Agent**：Codex 通过 ACP 接入，按 IM 会话隔离外部进程会话。
- **制品交付**：TaskWorkspace 保存任务文件，ArtifactStore 发布稳定制品，企业微信返回原生文件消息。
- **容量验证**：有界队列、Worker pool、503 背压和可调参数压测脚本。

## 架构

系统只有一个统一 `AgentGateway`，它把客户端请求路由到四种执行运行时，并把运行时的过程输出统一为 `GatewayEvent`：

1. **Agently Agent Runtime**：通用 Agent，自主使用 Search、Browse、Skills、Actions、TaskWorkspace 和沙盒。
2. **Question Workflow Runtime**：按固定 TriggerFlow 流程执行的问数服务，通过 HTTP/SSE 对外提供任务服务。
3. **Matrix Workflow Runtime**：社媒矩阵草稿服务。创作与回复是两套 TriggerFlow，产出带评理与降级轨迹的草稿包，P0 不发送。
4. **ACP Agent Runtime**：通过 ACP 连接外部 Codex Agent，并维护独立会话和进程生命周期。

搜索和文档生成是 Agently Agent 的能力，不是独立路由后端。

```mermaid
flowchart LR
    IM["企业微信"] --> WT["WeCom Transport"]
    WT --> GW["AgentGateway"]

    GW -->|"auto / agent"| AR["Agently Agent Runtime"]
    GW -->|"auto / question"| QR["Question Service Runtime"]
    GW -->|"auto / matrix"| MR["Matrix Service Runtime"]
    GW -->|"explicit / codex"| CR["ACP Agent Runtime"]

    AR --> ACT["Actions<br/>Search / Browse / Files"]
    AR --> SK["SkillLibrary<br/>model_decision"]
    AR --> SB["Sandbox<br/>Python / Shell"]
    AR --> TW["TaskWorkspace"]

    QR --> HTTP["Question HTTP API"]
    HTTP --> Q["Bounded Queue"]
    Q --> TF["TriggerFlow 问数流程"]
    TF --> SSE["Stable SSE Events"]
    SSE --> QR

    MR --> MHTTP["Matrix HTTP API"]
    MHTTP --> MQ["Bounded Queue"]
    MQ --> CF["COMPOSE_FLOW"]
    MQ --> RF["REPLY_FLOW"]
    CF --> MSSE["Stable SSE Events"]
    RF --> MSSE
    MSSE --> MR

    CR --> ACP["ACP Process + Session"]

    TW --> AS["ArtifactStore"]
    AS --> WT
    QR --> GW
    MR --> GW
    CR --> GW
```

依赖方向固定为：

```text
bootstrap → transports / gateway / runtimes / storage
transports → gateway contracts
gateway → runtime protocol
runtimes → gateway contracts
question analysis → 不依赖 HTTP、企业微信或 Gateway
matrix host → 不依赖 HTTP、企业微信、Gateway 或 question.analysis
```

`bootstrap` 可以认识所有模块，但只组装对象；任何业务规则都不能写回启动文件。

### Owner / Invariant

| Owner | Owns | Completion invariant |
|---|---|---|
| `AgentGateway` | 明确切换、自动路由、会话当前运行时、运行时注册表 | 模型只能返回宿主提供并校验过的 `runtime_key` |
| Intent ModelRequest | 在 `agent`、`question` 与 `matrix` 运行时卡片中做语义选择 | 不允许自动选择 Codex |
| `GatewayRequest` | 文本、会话和附件的客户端无关请求 | 运行时不读取企业微信原始 frame |
| `GatewayEvent` | 文本、状态、图表、证据、制品和终态事件 | Transport 不读取框架私有流事件 |
| Agently Agent Runtime | 通用 Agent 的一次执行与能力挂载 | 搜索、Skills、Actions、Workspace、沙盒在同一个 Agent 所有权内 |
| `SkillLibrary` | Skill 安装、不可变 revision 和资源读取 | 模型只从宿主提供的候选中选择 |
| `AgentExecution` | 当前请求的 Skill 选择、上下文准备和模型执行 | 模型选择后，宿主重新连接并校验 canonical revision |
| `ActionRuntime` | 搜索、文件生成、文件操作、Python 和 Shell 调用证据 | 没有成功 Action 记录就不能声称副作用完成 |
| `TaskWorkspace` | 单任务文件边界、路径 containment、写入和回读事实 | 文件发布前必须取得真实 bytes、size 和 SHA-256 |
| Sandbox | Python / Shell 的隔离执行和命令授权 | 默认要求 Docker，不允许隐式退回不受控本机执行 |
| `ArtifactStore` | 已验证文件的稳定发布副本与不透明 ID | 客户端不能通过下载 URL 访问任意服务器路径 |
| Question HTTP API | `/question` 页面；任务受理 `/v1/question/tasks`、状态、SSE、过载拒绝 | 受理任务最终进入 completed 或 failed |
| `QuestionTaskService` | 有界队列、Worker pool、任务状态 | 队列满时立即返回 503 和 Retry-After |
| Question Workflow | 五阶段问数、Evidence、ChartSpec 与 Trace | 最终答案和图表只使用本次运行证据 |
| Question Service Runtime | 把远端 SSE 翻译成 `GatewayEvent` | 消费到稳定终态后结束 |
| Matrix HTTP API | 创作/回复受理、状态、SSE、过载拒绝 | 入口绑定 COMPOSE_FLOW 或 REPLY_FLOW；禁止 auto/mixed |
| `MatrixTaskService` | 有界队列、Worker pool、任务状态 | 队列满时立即返回 503 和 Retry-After |
| ConstraintGate | AC、字数、引用、降级算子 | 硬策略失败不得进入 Review 的 accept 集合 |
| Matrix Service Runtime | 把远端 SSE 翻译成 `GatewayEvent` | `package.ready` 只映射一次 `message.delta` |
| ACP Agent Runtime | 每个 Gateway session 对应一个 ACP client | 不同 IM 会话不共享 ACP session |
| User / token | 注册登录；持久化走 `IdentityRepository`（SQLAlchemy + SQLite 或 MySQL）；`admin`/`user` 由 IdentityStore 强制 | 不是 Agently Session，也不是人设 `account_key` |
| Matrix conversation session | 登录用户 1:N 对话；`session_id` 即 Agently Session.id；仅矩阵工作台 | 不问数、不企业微信；不是 TriggerFlow 状态 |
| Matrix collection | 登录用户 1:N 收藏夹；推文与回复落 `collection_items`（`parent_item_id` 自引用） | 不问数、不企业微信；不是 localStorage 真相源 |
| WeCom Transport | 企业微信协议、流式快照和原生文件消息 | final 帧只发送一次，制品使用原生文件消息交付 |

### Node

| Node | Owner | Kind | Input | Output |
|---|---|---|---|---|
| normalize_request | Transport | deterministic | IM frame / HTTP body | `GatewayRequest` / `TaskCreate` |
| explicit_runtime | Gateway | deterministic | `/agent ...` | session runtime |
| classify_runtime | ModelRequest | semantic | text + offered runtime cards | `agent`、`question` 或 `matrix` |
| validate_runtime | Gateway | deterministic | selected key | registered runtime |
| select_skill | AgentExecution | semantic | task + offered Skill cards | selected revision ref or none |
| run_general_agent | AgentExecution | agent loop | text + mounted Actions | reply stream |
| run_document_action | ActionRuntime | side effect | selected Skill + sections | TaskWorkspace file facts |
| run_sandbox_action | ActionRuntime + sandbox | side effect | validated command | bounded Action result |
| publish_artifact | ArtifactStore | deterministic | verified workspace bytes | artifact ID |
| admit_question | HTTP API | deterministic | `TaskCreate` | 202 or 503 |
| execute_question | TriggerFlow | stable workflow | `TaskRequest` | answer + evidence + charts |
| adapt_question_sse | Question runtime | protocol translation | SSE events | `GatewayEvent` |
| admit_matrix | HTTP API | deterministic | `MatrixTaskCreate` | 202 or 503 |
| register_user | host identity + IdentityRepository | deterministic | username + password | user、token；SQLAlchemy 写入身份库 |
| execute_compose | COMPOSE_FLOW | stable workflow | compose request | 草稿包 + 评理 |
| execute_reply | REPLY_FLOW | stable workflow | reply request | 评理 + 回复草稿 |
| adapt_matrix_sse | Matrix runtime | protocol translation | SSE events | `GatewayEvent` |
| run_external_agent | ACP runtime | external session | explicit prompt | text deltas |
| present_event | Transport | outlet | `GatewayEvent` | IM stream / file message |

### Edge

| Source | Value / Event | Consumer | Policy |
|---|---|---|---|
| Transport | `GatewayRequest` | Gateway | 原始平台字段到此为止 |
| Intent model | short `runtime_key` | Gateway registry | 校验并重新连接宿主对象 |
| Gateway | request | selected runtime | Gateway 不解释运行时内部事件 |
| Skill selector | short selection key | SkillLibrary | 校验后解析 canonical revision |
| SkillLibrary | exact revision | AgentExecution | 选择完成后作为可用性契约绑定 |
| AgentExecution | Action call | ActionRuntime | schema、权限与策略校验 |
| ActionRuntime | generated bytes | TaskWorkspace | 先落工作区并完整回读 |
| TaskWorkspace | verified bytes + digest | ArtifactStore | 只发布已验证内容 |
| Question Workflow | stable task event | SSE | 不暴露 TriggerFlow 私有对象 |
| Question SSE | event payload | Question runtime | 翻译成 `GatewayEvent` |
| Matrix Workflow | stable task event | SSE | `package.ready` 只发布一次 |
| Matrix SSE | event payload | Matrix runtime | 翻译成 `GatewayEvent` |
| ACP client | text chunk | ACP runtime | 翻译成 `message.delta` |
| Runtime | `GatewayEvent` | WeCom presenter | 文本、图表提示和文件分别交付 |

### 必要性

| Element | Why required |
|---|---|
| 运行时级路由 | 路由选择执行模型；搜索、Skills 是 Agent 内部能力，不应伪装成服务 |
| 明确 Codex 切换 | 外部代码 Agent 权限更高，不能由普通业务消息自动进入 |
| Host candidate validation | 模型不拥有服务身份，也不能构造后端对象 |
| Canonical `GatewayEvent` | IM 不依赖 SSE、Agently 或 ACP 的私有事件格式 |
| Native Actions / Skills | 不重复实现 Agently 已拥有的选择、调用和证据机制 |
| Sandbox default | 命令执行必须有独立的隔离和授权边界 |
| TaskWorkspace / ArtifactStore 分离 | 工作文件与对外交付文件具有不同生命周期 |
| Bounded queue | 问数与矩阵模型过载时不能无限占用内存 |
| Stable SSE | Web 和 IM 可以独立消费同一任务事实 |
| Per-session ACP clients | 外部 Agent 的上下文不能跨用户串话 |
| IdentityRepository | `runtimes/matrix/host/db`：SQLAlchemy 异步 ORM 落盘；默认 SQLite，不把密码写入 Agently RecordStore；首个用户为 admin，其后公开注册为 user，管理接口按角色鉴权 |
| Thin bootstrap | 入口变化不会迫使业务模块重新组织 |

## 项目结构

```text
integrated_agent_service/
├── integrated_agent/
│   ├── gateway/                 # 统一请求、事件、路由和会话选择
│   ├── runtimes/
│   │   ├── agent/               # Agently Agent + Actions + Skills + Sandbox
│   │   ├── question/            # 问数任务服务、TriggerFlow 和分析流程
│   │   ├── matrix/              # 社媒矩阵草稿：compose / reply / kb_chat / host / rag
│   │   │   └── host/db/         # 身份库 ORM、异步仓储与 Alembic 迁移
│   │   └── acp/                 # Codex ACP client 与 session runtime
│   ├── storage/                 # 对外发布的制品
│   ├── transports/
│   │   ├── http/                # 问数与矩阵 HTTP/SSE
│   │   └── wecom/               # 企业微信消息和文件
│   └── bootstrap/               # 两个可部署进程的依赖组装
├── data/                        # 问数演示数据库与矩阵夹具
├── skills/                      # 文档 Skill 包
├── static/                      # 简易 SSE Web 客户端
├── tests/                       # 单元、集成和端到端契约
├── run_server.py                # 问数 HTTP/SSE 服务
├── run_im_assistant.py          # 企业微信 Gateway
├── run_file_skill_demo.py       # 文件工作区离线演示
└── load_test.py                 # 参数化压力测试
```

运行时生成的 `workspace/`、`logs/`、缓存和密钥文件不会进入版本库。

## 快速开始

所有命令都在本项目根目录运行。

### 1. 创建 Python 3.10+ 环境

```bash
python3.10 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

或者使用 Conda：

```bash
conda create -n integrated-agent python=3.10
conda activate integrated-agent
python -m pip install -r requirements.txt
```

### 2. 准备配置

```bash
cp .env.example .env
```

至少填写：

```text
DEEPSEEK_API_KEY=
```

连接企业微信时再填写：

```text
WECOM_BOT_ID=
WECOM_BOT_SECRET=
```

| 能力 | 额外条件 |
|---|---|
| 通用 Agent 搜索、Skills 与问数 | DeepSeek API |
| Python / Shell Action | 默认需要 Docker |
| 企业微信入口 | 企业微信智能机器人配置 |
| `/agent codex` | Node.js、`npx` 与可用的 Codex 登录状态 |

### 3. 启动问数 HTTP/SSE 服务

```bash
python run_server.py
```

默认地址：

```text
http://127.0.0.1:8000/          # 登录后进入矩阵草稿
http://127.0.0.1:8000/question  # 问数
http://127.0.0.1:8000/matrix    # 矩阵草稿（与根路径相同）
```

检查服务：

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/ready
```

接口：

```text
GET  /health
GET  /ready
GET  /
GET  /matrix
GET  /question
POST /v1/question/tasks
GET  /v1/question/tasks/{task_id}
GET  /v1/question/tasks/{task_id}/events
POST /api/create
POST /api/reply
POST /v1/matrix/tasks
GET  /v1/matrix/tasks/{task_id}
GET  /v1/matrix/tasks/{task_id}/events
GET  /v1/artifacts/{artifact_id}/{filename}
```

### 4. 启动企业微信入口

保持问数服务运行，再启动：

```bash
python run_im_assistant.py
```

运行时切换指令：

```text
/agent auto
/agent agent
/agent question
/agent matrix
/agent codex
```

`auto` 模式只在下面三个安全候选中做语义选择：

- `agent`：搜索、Skills、Actions、文件和沙盒任务。
- `question`：企业经营数据库问数。
- `matrix`：写推文、多平台草稿、回复评论与评理。

Codex 必须由用户明确切换。企业微信落到 matrix 时绑定创作 Flow；回评走 HTTP `comments[]`。

### 5. 调整沙盒策略

默认使用 Docker：

```text
AGENT_SANDBOX=docker
```

只有在明确理解风险的本地开发环境中，才改为：

```text
AGENT_SANDBOX=trusted_local
```

Codex 权限请求默认不自动批准。仅在受控演示环境中设置：

```text
CODEX_AUTO_APPROVE=true
```

## 身份库与数据库操作

矩阵登录用户、对话会话、用户轮次和收藏夹默认落 SQLite：`workspace/identity/identity.sqlite`（不入库）。`.env` 里 `IDENTITY_DB=sqlite` 或 `mysql` 切换后端。代码在 `integrated_agent/runtimes/matrix/host/db/`：

```text
db/
├── models.py          # SQLAlchemy ORM 表与 Stored* DTO
├── settings.py        # IDENTITY_DB / IDENTITY_SQLITE / IDENTITY_MYSQL_URL
├── repository.py      # 异步仓储（AsyncSession + SQLite 或 MySQL）
├── alembic.ini
└── migrations/        # Alembic 版本脚本
```

`identity.py` 只做注册登录、会话与收藏夹业务；读写都走异步 `IdentityRepository`。表关系：`users` 1:N `sessions`，`sessions` 1:N `session_turns`；`users` 1:N `collections`，`collections` 1:N `collection_items`（`parent_item_id` 自引用，推文下挂回复，删除级联）。

收藏夹 HTTP（需登录，Bearer / `X-User-Token`，只读写当前用户自己的数据）：

```text
GET    /api/collections
POST   /api/collections                      { "name": "秋天系列" }
GET    /api/collections/{id}
DELETE /api/collections/{id}
POST   /api/collections/{id}/items           { "items": [...], "bind_replies": false }
DELETE /api/collections/{id}/items/{item_id}
```

`bind_replies=true` 时按 `parent_key` / `parent_text` 把条目挂到原推下；找不到原推且有 `parent_text` 则新建一条原推。下载仍在浏览器用已拉取的数据打包，不另开下载接口。

所有命令在项目根目录执行。Alembic 与运行时读同一套 `.env`。

```bash
# 默认 SQLite
IDENTITY_DB=sqlite
# IDENTITY_SQLITE=workspace/identity/identity.sqlite

# 切到 MySQL（先建空库 matrix_identity，utf8mb4）
IDENTITY_DB=mysql
IDENTITY_MYSQL_URL=mysql+asyncmy://user:pass@127.0.0.1:3306/matrix_identity
```

```bash
# 升级到最新（空库会建 users / tokens / sessions / session_turns / collections / collection_items）
# 001、002 对已存在的表幂等，旧 SQLite 可直接 upgrade
alembic upgrade head

# 改完 db/models.py 后生成新版本并升级
alembic revision --autogenerate -m "describe change"
alembic upgrade head

# 回退一步
alembic downgrade -1

# 当前版本 / 历史
alembic current
alembic history
```

也可以显式指定配置文件：

```bash
alembic -c integrated_agent/runtimes/matrix/host/db/alembic.ini upgrade head
```

换 SQLite 路径：

```bash
IDENTITY_SQLITE=/abs/path/identity.sqlite alembic upgrade head
```

## 压力测试

查看参数：

```bash
python load_test.py --help
```

运行示例：

```bash
python load_test.py \
  --requests 20 \
  --client-concurrency 5 \
  --workers 2 \
  --queue-capacity 8 \
  --worker-delay-ms 200
```

脚本观察 202、503、SSE 终态、峰值 Worker、提交延迟和完成情况。`TimedWorker` 只隔离验证服务并发，不代表真实模型吞吐。

## 验证

```bash
python -m pip install -r requirements-dev.txt
pyright --pythonpath "$(command -v python)"
pytest -q
```

测试覆盖 Gateway 安全候选（含 matrix）、附件归一、问数终态、矩阵草稿包、SSE、背压、图表、Skill → Action → Workspace → Artifact 链路、ACP 会话隔离和企业微信文件协议。

## 迁移到自己的项目

1. 新增执行方式时，实现 `AgentRuntime.stream(GatewayRequest)`，不要修改 Transport。
2. 新增普通能力时，优先作为 Agently Action、Skill 或 ExecutionResource 挂到 `AgentlyAgentRuntime`。
3. 只有拥有独立任务生命周期、压力边界或外部会话协议时，才新增 Runtime。
4. 保留 `GatewayEvent` 作为所有客户端共同消费的稳定事件契约。
5. 将 `transports/wecom/` 替换为其他 IM 平台时，不修改运行时内部逻辑。
