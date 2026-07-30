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
│   │   └── acp/                 # Codex ACP client 与 session runtime
│   ├── storage/                 # 对外发布的制品
│   ├── transports/
│   │   ├── http/                # 问数 HTTP/SSE
│   │   └── wecom/               # 企业微信消息和文件
│   └── bootstrap/               # 两个可部署进程的依赖组装
├── data/                        # 问数演示数据库
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
http://127.0.0.1:8000/
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
POST /v1/tasks
GET  /v1/tasks/{task_id}
GET  /v1/tasks/{task_id}/events
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
/agent codex
```

`auto` 模式只在下面两个安全候选中做语义选择：

- `agent`：搜索、Skills、Actions、文件和沙盒任务。
- `question`：企业经营数据库问数。

Codex 必须由用户明确切换。

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

测试覆盖 Gateway 安全候选、附件归一、问数终态、SSE、背压、图表、Skill → Action → Workspace → Artifact 链路、ACP 会话隔离和企业微信文件协议。

## 迁移到自己的项目

1. 新增执行方式时，实现 `AgentRuntime.stream(GatewayRequest)`，不要修改 Transport。
2. 新增普通能力时，优先作为 Agently Action、Skill 或 ExecutionResource 挂到 `AgentlyAgentRuntime`。
3. 只有拥有独立任务生命周期、压力边界或外部会话协议时，才新增 Runtime。
4. 保留 `GatewayEvent` 作为所有客户端共同消费的稳定事件契约。
5. 将 `transports/wecom/` 替换为其他 IM 平台时，不修改运行时内部逻辑。
