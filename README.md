# PR-RSI Harness

**Self-Evolving Multi-Agent Harness for PR Risk Control** —— 面向研发流程中 Pull Request 提交环节的风险管控与安全修复系统。以多智能体协作端到端完成风险识别、证据核验、安全修复、结果校验与自动化评估，并通过反馈闭环驱动 Prompt 能力受控迭代演进。

## 核心特性

### 1. 高可用 Runtime Harness 与全链路 Trace

- 统一任务状态机（`PENDING → RUNNING → CHECKPOINT → VERIFYING → FIXING → VALIDATING → DONE`），非法流转直接拒绝
- 基于 ReAct 范式的**有界 Agent 执行循环**，受步数/时间双预算约束
- 容错调度：Checkpoint 保存/加载、**指数退避重试**、**死信队列**、断点续练
- 全链路 Trace 记录 Agent 交互与状态流转，支持**链路回放**、评测复现与**细粒度失败归因**（定位失败智能体/步骤，关联死信原因与预算终止原因）
- Prometheus 指标：任务结果计数、耗时直方图、重试、死信

### 2. 反馈驱动自进化 RSI 与端到端评测

- 误报/漏报自动转为**结构化反馈**（含失败轨迹），评测过程逐 case 自动落库
- 失败按标签**自动聚类**，驱动 Prompt/Skill **候选版本生成**（LLM 优先，无 Key/调用失败回退确定性规则模板）
- 候选版本管理：内容级**去重**、单活**激活**、**回滚**
- **提升 + 非退化门禁**：F1/高风险召回/干净 PR 准确率任一退化即拒绝上线，无门禁数据绝不自动生效
- 100 条受控 PR Diff 基准集，按仓库隔离划分 Validation / Holdout

### 3. 上下文压缩与 Memory 管理

- 按风险优先级对超大 Diff 自适应压缩（高风险 hunk 优先保留）
- **Token 预算内动态组装上下文**：高风险 hunk > Critic 反馈 > 工具描述 > 历史观测，超预算按优先级截断
- 「工作 - 情景 - 语义」三层记忆，长短记忆打通；Critic 校验意见写入工作记忆，回流至后续任务的上下文
- 租户/仓库隔离检索、任务归档与过期清理

### 4. 存储与事件流

- **双后端存储**：SQLite（本地/评测，零依赖）与 PostgreSQL（`GENERATED AS IDENTITY`、`dict_row`、占位符方言适配），按连接串自动选择
- **Redis Streams 事件总线**：状态变更与 Agent 交互事件自动发布（XADD），支持读取回放；总线故障静默降级，不阻断主流程

## 快速开始

```bash
pip install -e .                         # 基础安装（SQLite 后端，离线可跑）
pip install -e ".[webhook]"              # 追加 Webhook 服务依赖（FastAPI + Uvicorn）
pip install -e ".[postgres,redis,dev]"   # 完整安装
```

无 `SPARK_API_KEY` 时系统使用确定性 EchoLLM 与规则扫描器离线运行，适合开发与测试。

### CLI

```bash
# 提交 PR Diff 审查
pr-harness --db data/harness.db submit --repo acme/api --pr 7 --branch feat --diff-file pr.diff

# 直接从 Git 仓库拉取 PR diff 并审查（支持 GitHub / GitLab / 本地仓库路径）
pr-harness --db data/harness.db submit-from-git \
  --repo-url https://github.com/acme/api.git --pr 7 --branch main --token <TOKEN>

# 断点续练
pr-harness --db data/harness.db resume --task-id <task_id>

# 运行评测基准（100 条，输出报告 data/eval_report.json）
pr-harness --db data/harness.db eval

# 自进化：聚类失败 -> 生成候选 -> 评测门禁 -> 自动上线/回滚
pr-harness --db data/harness.db evolve

# 链路回放与失败归因
pr-harness --db data/harness.db replay --task-id <task_id>

# 死信队列 / 事件流 / Prometheus 指标
pr-harness --db data/harness.db dead-letters
REDIS_URL=redis://localhost:6379/0 pr-harness --db data/harness.db events
pr-harness --db data/harness.db serve --port 8000
```

`--db` 同时接受 SQLite 路径与 `postgresql://` 连接串。

### 接入方式：pre-push 钩子、CI 与 Webhook

**本地 pre-push 钩子**：push 前自动审查本次推送的 diff，findings 直接打印在终端。

```bash
cp scripts/pre-push .git/hooks/pre-push         # Git Bash / Linux / macOS
Copy-Item scripts/pre-push .git/hooks/pre-push  # PowerShell
```

- 已有分支以远端引用为基线做 `base...HEAD` diff，新分支回退到 `origin/HEAD`；删除分支、无改动自动跳过
- 默认只提示不拦截 push，设置 `PR_HARNESS_BLOCK=1` 后执行失败才阻止推送
- 可选环境变量：`PR_HARNESS_DB`、`PR_HARNESS_REPO`、`PR_HARNESS_PR`、`PR_HARNESS_PYTHON`

**GitHub Actions**：`.github/workflows/pr-risk-review.yml` 在 PR 的 `opened / synchronize / reopened` 时自动运行风险审查，结果写入 Job Summary，并上传 `pr.diff` 与 `review.json` artifact。

- **风险门禁**：检出 findings 时以 `::warning::` 输出告警并使 job 失败，PR 检查不通过；配合分支保护规则即可阻止高危 PR 合并。未产生评审结果同样判定失败
- 权限为 `contents: read` + `pull-requests: write`，告警以检查注解形式展示，不自动创建 PR 评论
- 在仓库 Secrets 配置 `SPARK_API_KEY` 即可启用 LLM 增强，未配置时走离线规则扫描

**GitLab CI**：`.gitlab-ci.yml` 在 `merge_requests` 流水线中生成 MR diff 并运行审查，findings 非空时任务失败以阻止合并，`pr.diff` 与 `data/review.json` 作为 artifact 保留 7 天。

### Webhook 服务（自动接收 PR / MR 事件）

安装 `webhook` extra 后可启动常驻服务，接收 GitHub / GitLab 事件并自动拉取 diff 审查：

```bash
python scripts/run_webhook.py       # 默认监听 0.0.0.0:8080
```

| 端点 | 说明 |
| --- | --- |
| `POST /webhook/github` | 接收 `pull_request` 事件，校验 `X-Hub-Signature-256`（HMAC SHA256） |
| `POST /webhook/gitlab` | 接收 `Merge Request Hook`，校验 `X-Gitlab-Token` |
| `GET /health` | 健康检查 |

- 只处理 `opened / synchronize / reopened`（GitHub）与 `open / update / reopen`（GitLab），其他事件或动作返回 200 `ignored`
- 事件 ID 内存幂等（TTL 1 小时），避免同一 PR 重复触发；事件返回 202 后由后台任务异步完成 diff 拉取与审查
- 配置 `GITHUB_TOKEN` / `GITLAB_TOKEN` 后，会把风险报告评论回写到 PR / MR（findings 列表 + 建议修复补丁）；未配置则仅记录日志

## 配置

| 环境变量 | 说明 |
| --- | --- |
| `SPARK_API_KEY` | 讯飞 Spark API Key，未设置时使用 EchoLLM 离线运行 |
| `SPARK_MODEL` / `SPARK_BASE_URL` | 模型名与 OpenAI 兼容接口地址 |
| `REDIS_URL` | 设置后自动挂载 Redis Streams 事件总线 |
| `TEST_PG_DSN` | 运行 PostgreSQL 集成测试（未设置时对应测试跳过） |
| `HARNESS_DB` | Webhook 服务使用的存储连接串（默认 `data/harness.db`） |
| `WEBHOOK_HOST` / `WEBHOOK_PORT` | Webhook 监听地址与端口（默认 `0.0.0.0:8080`） |
| `WEBHOOK_RELOAD` | 设为 `true` 时开启 Uvicorn 热重载 |
| `GITHUB_WEBHOOK_SECRET` | GitHub Webhook 签名密钥，未设置时拒绝所有 GitHub 事件 |
| `GITLAB_WEBHOOK_TOKEN` | GitLab Webhook 令牌，未设置时拒绝所有 GitLab 事件 |
| `GITHUB_TOKEN` / `GITLAB_TOKEN` | PR / MR 评论回写用的 API 令牌，未设置时跳过回写 |

## Docker 部署

```bash
docker compose -f deploy/docker-compose.yml up --build -d
```

编排内容：`harness`（Prometheus 指标 :8000，PG 后端，Redis 事件流）+ `webhook`（:8080，接收 GitHub / GitLab 事件，PG 后端 + Redis 事件流）+ `postgres` + `redis` + `prometheus`（:9090，抓取 harness 指标）。PG schema 首次启动自动创建。Webhook 密钥通过 `GITHUB_WEBHOOK_SECRET` / `GITLAB_WEBHOOK_TOKEN` 环境变量注入。

## 评测与自进化闭环

```bash
pr-harness eval     # 1. 逐 case 评测，FP/FN 自动落 feedback 表，生成指标报告
pr-harness evolve   # 2. 聚类失败 -> 生成 Prompt 候选 -> 去重 -> 试探激活 -> 重评测
                    #    门禁通过保留 active，失败自动回滚
```

### 真实执行反馈的自进化

真实 PR 审查执行也能驱动 RSI 自进化，不局限于评测基准集：

- `PipelineResult` 可携带可选 `review_feedback`，`HarnessRuntime` 在真实任务执行成功后通过 `ReviewFeedbackCollector` 把它写入同一 `feedback` 表（findings 作为 predicted，人工/策略给定的期望风险集作为 expected）。
- 评测反馈与真实执行反馈共用 `feedback` 表的聚类与门禁进化闭环（`cluster_failures` → 生成候选 → 去重 → 试探激活 → 重评测 → 门禁 → 激活/回滚）。
- 也可以手动记录真实执行反馈：`pr-harness review-feedback --task-id <task_id> --predicted '<json>' --expected '<json>' --confidence 0.5 --trajectory '<json>'`。
- 评测仍作为基线、门禁把关与持续学习基准保留。

## 测试

```bash
pytest                        # 全量（PG/Redis 集成测试按环境变量自动跳过）
TEST_PG_DSN=postgresql://harness:harness@localhost:5432/harness pytest tests/test_storage_pg.py
REDIS_URL=redis://localhost:6379/0 pytest tests/test_events.py
```

## 项目结构

```
src/
├── harness/          # 状态机、ReAct 循环、双预算、重试、统一调度
├── agent/            # 四智能体流水线、规则扫描器、LLM 封装（Echo/Spark）
├── memory/           # Diff 压缩、上下文组装器、三层记忆
├── feedback/         # RSI 引擎、候选生成器（LLM+模板回退）
├── eval/             # 基准集、指标计算、评测编排
├── git/              # 从 GitHub / GitLab / 本地仓库提取 PR diff
├── webhook/          # FastAPI 服务、签名验证、幂等与评论回写
├── observability/    # Prometheus 指标、Trace 回放与失败归因
├── storage/          # 存储抽象、SQLite/PG 后端、Redis Streams 事件总线
└── cli.py            # 统一命令行入口
deploy/               # Dockerfile、Dockerfile.webhook、docker-compose、prometheus.yml
scripts/              # pre-push 钩子、Webhook 启动脚本
.github/workflows/    # GitHub PR 风险审查 CI
.gitlab-ci.yml        # GitLab MR 风险审查流水线
tests/                # 单元测试 + 可选 PG/Redis 集成测试
```
