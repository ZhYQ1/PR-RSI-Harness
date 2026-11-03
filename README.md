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
pip install -e .            # 基础安装（SQLite 后端，离线可跑）
pip install -e ".[postgres,redis,dev]"   # 完整安装
```

无 `SPARK_API_KEY` 时系统使用确定性 EchoLLM 与规则扫描器离线运行，适合开发与测试。

### CLI

```bash
# 提交 PR Diff 审查
pr-harness --db data/harness.db submit --repo acme/api --pr 7 --branch feat --diff-file pr.diff

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

## 配置

| 环境变量 | 说明 |
| --- | --- |
| `SPARK_API_KEY` | 讯飞 Spark API Key，未设置时使用 EchoLLM 离线运行 |
| `SPARK_MODEL` / `SPARK_BASE_URL` | 模型名与 OpenAI 兼容接口地址 |
| `REDIS_URL` | 设置后自动挂载 Redis Streams 事件总线 |
| `TEST_PG_DSN` | 运行 PostgreSQL 集成测试（未设置时对应测试跳过） |

## Docker 部署

```bash
docker compose -f deploy/docker-compose.yml up --build -d
```

编排内容：`harness`（Prometheus 指标 :8000，PG 后端，Redis 事件流）+ `postgres` + `redis` + `prometheus`（:9090，抓取 harness 指标）。PG schema 首次启动自动创建。

## 评测与自进化闭环

```bash
pr-harness eval     # 1. 逐 case 评测，FP/FN 自动落 feedback 表，生成指标报告
pr-harness evolve   # 2. 聚类失败 -> 生成 Prompt 候选 -> 去重 -> 试探激活 -> 重评测
                    #    门禁通过保留 active，失败自动回滚
```

指标口径说明：基准集为规则生成的受控 Diff（与扫描器同源），当前实测 F1/召回/干净准确率均为 1.0，验证的是 Harness 全链路与自进化闭环的正确性；计划目标口径（F1 91.3%、高风险召回 94.7%、干净 PR 准确率 91.7%，基线 81.9%/84.2%/90.0%）需在真实 Diff 数据上重测方有业务意义。

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
├── observability/    # Prometheus 指标、Trace 回放与失败归因
├── storage/          # 存储抽象、SQLite/PG 后端、Redis Streams 事件总线
└── cli.py            # 统一命令行入口
deploy/               # Dockerfile、docker-compose、prometheus.yml
tests/                # 单元测试 + 可选 PG/Redis 集成测试
```
