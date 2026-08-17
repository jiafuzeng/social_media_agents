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

## 项目结构

```text
integrated_agent_service/
├── integrated_agent/
│   ├── gateway/                 # 统一请求、事件、路由和会话选择
│   ├── runtimes/
│   │   ├── agent/               # Agently Agent + Actions + Skills + Sandbox
│   │   ├── question/            # 问数任务服务、TriggerFlow 和分析流程
│   │   ├── matrix/              # 社媒矩阵草稿、两套 TriggerFlow 与硬门
│   │   │   └── db/              # 身份库 ORM、异步仓储与 Alembic 迁移
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
├── load_test.py                 # 参数化压力测试
└── ARCHITECTURE.md              # Owner、Node、Edge 与必要性账本
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

矩阵登录用户、对话会话、用户轮次和收藏夹落在 SQLite：`workspace/identity/identity.sqlite`（不入库）。代码在 `integrated_agent/runtimes/matrix/db/`：

```text
db/
├── models.py          # SQLAlchemy ORM 表与 Stored* DTO
├── repository.py      # 异步仓储（AsyncSession + aiosqlite）
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

所有命令在项目根目录执行。

```bash
# 升级到最新（空库会建 users / tokens / sessions / session_turns / collections / collection_items）
# 001、002 对已存在的表幂等，旧库可直接 upgrade
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
alembic -c integrated_agent/runtimes/matrix/db/alembic.ini upgrade head
```

换库路径：

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
