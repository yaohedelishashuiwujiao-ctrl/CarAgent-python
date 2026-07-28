# 生产级 Agent PRD 实施状态

更新日期：2026-07-16

对应 PRD：[`PRODUCTION_AGENT_GAP_PRD.md`](./PRODUCTION_AGENT_GAP_PRD.md)

新增设计：[`TOOL_ROUTING_AND_DATA_ANALYSIS_CAPABILITY_DESIGN.md`](./TOOL_ROUTING_AND_DATA_ANALYSIS_CAPABILITY_DESIGN.md)

## 工具路由 Phase 0（2026-07-15）

| 项目 | 状态 | 实现 |
|---|---|---|
| 统一 SQL 执行内核 | 已完成 | `SubjectsSqlQuery` 与 `AutoChartGenerate.sql_query` 统一复用 AST、表白名单、data scope、EXPLAIN、超时、返回上限和 evidence hash；图表不再存在独立 SQL 旁路。 |
| 结构化工具结果 | 已完成兼容底座 | 新增 `ToolOutcomeStatus`、`reason_code`、`retryable` 和 diagnostics；旧工具只设置 `is_error` 时自动归一为稳定错误状态。 |
| 工具结果大小执行 | 已完成首版 | `ToolRegistry.dispatch` 现在执行 `max_result_size_chars`；超限结果生成有界预览并标记 `partial_success/RESULT_SIZE_LIMIT`，不再把完整大结果送入模型上下文。后续阶段将升级为外置 data handle。 |
| Tool Capability Contract | 已完成生产工具首版 | Phase 0 的 11 个生产工具均声明 namespace、actions、entity/input/output modes、limitations、执行资源池、依赖和 preflight checks；加入 `ToolSearch` 与业务字段覆盖目录后，生产 Profile 为 13 个工具。 |
| 真实边界可见 | 已完成首项 | `WebFetch` ToolSpec 动态公开当前域名 allowlist，并明确未授权域名不能调用。 |
| 结果可观测性 | 已完成首版 | Tool result 事件和 NDJSON 流新增 outcome status、reason code、retryable；日志可区分无数据、权限拒绝、输入问题和暂时故障。 |

## 工具路由 Phase 1（2026-07-16）

| 项目 | 状态 | 实现 |
|---|---|---|
| 动态 Eligibility | 已完成首版 | 每轮只向模型暴露当前 Job allowlist、permission context、结构化 `eligibility` 和工具 `is_enabled` 均允许的 ToolSpec；生产 MySQL 未配置/配置非法/客户端缺失时 SQL 工具不会进入候选集，运行中确认不可用的工具也会从本次 Run 移除。尚未接入实时依赖健康/数据覆盖探针。 |
| 统一 Preflight | 已完成首版 | Registry 在执行前统一做 Eligibility、JSON Schema、工具参数边界和权限判断；SQL、Chart、PPT、WebFetch、Knowledge 已接入确定性预检。域名、SQL scope、PPT 页数/媒体等注定失败路径不进入真实执行；PPT 调用页数还会与用户原始 Output Contract 交叉校验。 |
| 结构化拒绝与审计 | 已完成 | Preflight 拒绝返回稳定 reason code、outcome status、retryable 和替代能力提示；拒绝不计入实际执行熔断器；Allowlist/权限拒绝仍保留审计记录。 |
| Requirement State / Output Contract | 已完成制品首版 | Runtime 从请求识别 PPT/PPTX、独立图表和结构化 JSON 要求；文字回复不能伪装成完成。PPTX 会实际打开 ZIP 并核对 slide XML 数量，图表核对文件存在且非空。事实槽位、数据覆盖和质量要求仍待扩展。 |
| Job 完成语义 | 已完成首版 | Runtime 通过 API/NDJSON 返回 `output_contract_status` 与 requirements；Backend 在 required artifact 未满足时把 Job 标记为 `FAILED`，不再标记 `SUCCEEDED`。 |
| 正常终止控制 | 已完成首版 | 删除“默认 6 次工具调用即停止”的正常预算语义；默认改为 24 次高位事故熔断器。正常停止依据为重复调用、重复结果、连续无进展、能力/权限/依赖边界及输出契约；不同且持续产生新证据的调用不会因固定小次数被截断。 |

## 工具路由 Phase 2（渐进式发现首版，2026-07-16）

| 项目 | 状态 | 实现 |
|---|---|---|
| 主能力/回退能力分层 | 已完成首版 | 非 general 任务首轮只加载 Route 的 preferred Tool Schema；只有 `NO_DATA`、coverage、dependency、capability、permission 边界或 Output Contract 未满足时才扩展 fallback，减少无关工具干扰。 |
| Capability-aware ToolSearch | 已完成首版 | `ToolSearch` 已进入生产安全 Profile；按 capability namespace、action、entity、input/output mode、正反例、名称和描述检索，并只返回当前 ToolContext 下 Eligibility 通过的工具。 |
| Harness deferred loading | 已完成首版 | 当前 Provider 不统一支持厂商原生 deferred schema，因此 Runtime 先暴露紧凑的 `ToolSearch`；命中的路由外工具在下一轮才加载完整 Schema。传输层后续可替换为厂商原生 Tool Search，不改变 Registry/Eligibility/Preflight。 |
| 候选集审计 | 已完成首版 | 每次候选工具集合改变都会记录 route、discovery stage、tool names、候选数和扩展原因；可用于计算候选召回率、错误扩展率及 schema token 节省。 |
| 权限不泄漏 | 已完成 | ToolSearch 先执行 Eligibility；未授权、被 permission context 阻止或依赖不可用的工具不会出现在搜索结果。 |

尚未完成、不得提前标记：embedding/语义重排与黄金查询学习、厂商原生 deferred loading 适配、实时 dependency health/coverage probe、多事实槽位/来源数量/时效性 Requirement、按 ToolExecutionPolicy 统一超时/重试/取消/缓存、Runtime 资源池调度和独立只读调用并行执行。

## 数据分析工具补齐 Phase 3（进行中，2026-07-16）

| 项目 | 状态 | 实现 |
|---|---|---|
| 业务字段覆盖目录 | 已完成 vehicle 首版 | 新增生产工具 `SubjectsDataCatalogSearch`，按业务关键词返回字段定义、单位、当前授权范围内覆盖车型数和值数量；字段不存在或有定义但无值时返回 `DATA_COVERAGE_INSUFFICIENT` 和明确边界。 |
| 路由接入 | 已完成 | vehicle/trend/artifact 路由优先使用业务目录判断覆盖，再决定业务查询、通用 SQL 或外部证据；物理 Schema/Glob 降为 fallback，减少探索 SQL。 |
| 数据范围 | 已完成 vehicle 首版 | 覆盖计数通过 active vehicle 与 ToolContext data scope 约束；system scope 不会回退到无范围的全局计数，而是返回需要专用 system catalog 的能力边界。 |
| 事实型 Requirement State | 已完成首版 | vehicle fact 必须有 SQL/业务查询/目录证据，manual QA 必须有文档或受控网页证据，trend/research 必须有受治理证据；调研型制品同时要求内容证据与有效文件。纯模型记忆文本不会满足任务。 |
| 完成状态拆分 | 已完成 | `output_contract_status` 只描述制品/结构化输出；`task_contract_status` 描述整个任务。Backend 以 Task Contract 判定成功或失败，requirements 返回 evidence kinds、最低数量和 Harness citation ids。 |
| Final 提交门禁 | 已完成 | Runtime 的 final frame 不再直接转发给前端；Backend 先校验 Task Contract，再发布唯一 final。未满足时发布 failed，修复“数据库中 Job 失败但前端已经显示完成”的竞态。 |

尚未完成：component/system catalog、语义指标层、可信查询库、用户文件分析、数据剖析/清洗、多源融合、统计分析、结构化 CSV/XLSX/Parquet 导出和 Vision Runtime 工具。

## 工具执行控制 Phase 4（首版，2026-07-16）

| 项目 | 状态 | 实现 |
|---|---|---|
| 统一执行策略 | 已完成首版 | Registry 统一执行 `ToolExecutionPolicy`；`timeout_s`、`max_attempts`、`retryable_outcomes`、`idempotent`、`side_effect` 和 `concurrency_pool` 不再只是声明字段。 |
| 精确重试 | 已完成首版 | 只有调用幂等、工具结果明确 `retryable=true`、outcome 命中工具白名单且仍有 attempts 时才重试；输入、权限、能力、覆盖不足、非幂等制品和 Runtime detached timeout 均不重试。 |
| Runtime 资源池 | 已完成进程内首版 | SQL、Web、Knowledge、Artifact、通用 Tool 使用共享有界线程池和有界排队；池满快速返回 `RESOURCE_POOL_SATURATED`，不把请求无限堆在内存中。 |
| 超时语义 | 已完成首版 | 对无副作用幂等调用执行 Runtime deadline；超时后返回 `TIMEOUT` 且不重复调用。底层线程若不能中断，会继续占用池容量直到实际结束，避免错误释放容量造成雪崩。制品工具仍依赖工具内部合作式超时。 |
| Attempt 审计 | 已完成 | 每次 attempt 记录工具、资源池、耗时、outcome、reason、是否重试和 timeout enforcement mode；最终 ToolResult diagnostics 返回 attempt_count、总耗时和资源池。 |
| 取消传播 | 已完成合作式首版 | Backend 取消运行中 Job 时，先用独立 control executor 调用 Runtime `/api/cancel`，再关闭流；Agent Loop 在模型返回、轮次边界和工具边界检查取消，工具等待期间约每 100ms 检查。无法强杀的底层线程继续占用池位直到退出。 |

尚未完成：进程隔离的硬超时/强制终止、Provider SDK 原生取消、跨 Runtime 实例的分布式工具资源池、独立只读工具并行 DAG 调度，以及生产级缓存/熔断状态存储。

## 本次已实施

| Epic | 状态 | 实现 |
|---|---|---|
| E1 身份与数据范围 | 已形成代码闭环 | Job 身份只取受信请求上下文，不再信任请求体；保存角色、数据范围和工具权限快照；Backend 向 Runtime 签发短时 HMAC Token；Runtime 校验签名、issuer、audience、session、过期时间并阻止重放；RAG 和结构化查询继承数据范围。 |
| E2 工具与 SQL 安全 | 已形成代码闭环 | 生产 Profile 默认移除 Bash、文件写入、任意用户工具和 Agent 子任务工具；每次工具调用执行 allowlist 授权并生成审计事件；通用 SQL 接入 `sqlglot` AST、表白名单、单语句限制、`EXPLAIN` 成本门禁、执行超时、返回上限和证据哈希；生产 WebFetch 仅允许 HTTPS 白名单域名并检查私网、重定向和响应大小。 |
| E3 自动上下文工程 | 已形成首版闭环 | 每次模型请求计算 System Prompt、Tool Schema、历史消息和输出预留预算；软阈值外置旧工具结果，硬阈值按完整 tool-call/tool-result 原子单元裁剪；生成固定 Schema 快照并保存压缩指标。 |
| E4 证据与引用 | 已形成首版闭环 | Harness 为证据计算哈希；最终答案拆分原子 claim，建立 claim→citation 映射，检查非法引用、数值一致性和文本支持度；高风险不一致自动附加人工复核提示；Evidence、Claim 与最终元数据持久化。 |
| E5 任务一致性 | 已形成代码闭环 | tenant+user+idempotency key 唯一约束；统一状态迁移校验；execution token + 单调 fencing token；MySQL 状态与 dispatch outbox 同事务写入；过期 Worker 结果受 fencing 保护；SSE 支持 `Last-Event-ID`。 |
| E6 资源与高并发 | 沿用并加固 | 保留模型、SQL、工具、制品独立 semaphore，DRR、公平队列、会话锁、租约、背压和 Redis Streams；本次将持久化 dispatch 边界接入 outbox。 |
| E7 模型与工具路由 | 首版 | 现有业务路由输出版本号并写入最终元数据；工具 Profile 与任务路由共同收敛工具面。模型健康度、成本和敏感等级驱动的多模型路由仍需接入真实部署。 |
| E8 RAG 数据治理 | 首版 | 生产检索只接受 `published` 文档；chunk 携带文档版本、解析器、密级和 ACL 元数据；迁移增加文档版本发布状态机。 |
| E9 可观测与审计 | 已形成首版闭环 | 所有 HTTP 响应携带 `X-Trace-ID`；提供请求量及 p50/p95/p99 快照；Job、Trace、Tool 授权决定、Context 压缩、Evidence 和 Claim 可关联。 |
| E10 评测与发布门禁 | 首版 | `scripts/agent_release_gate.py` 对安全、越权、引用、证据、数值、路由、RAG 和任务成功率指标执行 fail-closed 门禁。 |

## 数据库变更

执行 `database/migrations/20260715_agent_production_foundation.sql`。新增或扩展：`agent_chat_jobs`、`agent_job_outbox`、`agent_evidence_snapshot`、`agent_claim`、`agent_tool_audit`、`agent_context_snapshot`。

## 上线前必须配置

```dotenv
APP_ENV=production
ALLOW_INSECURE_DEV_AUTH=false
RUNTIME_TOKEN_SECRET=<至少32字节随机值>
API_TOKEN_SECRET=<与Runtime不同的至少32字节随机值>
CLAWD_TOOL_PROFILE=production
DATA_BACKEND=mysql
AGENT_JOB_BROKER=redis
SUBJECTS_DATABASE_URL=<仅有授权视图SELECT权限的数据库账号>
SUBJECTS_SQL_ALLOWED_TABLES=<生产允许表或视图>
```

安装依赖：`pip install -r backend/requirements.txt` 和 `pip install -r agent_runtime/requirements.txt`。缺少 `sqlglot` 时，生产环境会拒绝通用 SQL，不会回退到正则放行。

## 验证结果

- 生产专项测试：17 passed。
- Agent Runtime：301 passed（保留 4 个既有 unittest 异步测试写法警告）。
- 新增专项覆盖：Chart SQL 数据范围拒绝、生产工具能力契约完整性、WebFetch 真实域名边界和执行前拒绝、Registry 结果大小限制、结构化错误默认归类、Preflight 拒绝不消耗执行熔断器、生产性多源调用不因固定次数停止、真实 PPTX 页数校验，以及输出契约未满足时 Job 失败。
- 真实 MySQL → 受控 SQL → AutoChartGenerate 冒烟成功，返回 5 行、生成 5 个柱状数据点并携带 SQL evidence hash。
- 真实 MySQL 业务目录 SQL 已验证：`前悬架类型` 命中受治理字段，当前 active vehicle 覆盖 70 条；无覆盖字段由工具返回结构化 coverage boundary。
- 前端 TypeScript 检查和 Vite production build 通过。
- Backend、Runtime 和 Web 入口静态编译通过。
- 原有 `tests/test_core.py`、`tests/test_knowledge.py` 的 FastAPI `TestClient` 请求在当前 Python 3.14/沙箱组合中挂起，未出现断言失败；独立 HTTP Job/SSE 冒烟测试已通过。

## 尚需真实环境验收

- 在真实 MySQL/Redis 上执行迁移、故障注入和多实例恢复测试。
- 用数据库只读账号验证 DDL/DML 在数据库层被拒绝。
- 安装 `sqlglot` 后运行 SQL 绕过安全集和扫描预算用例。
- 用真实模型黄金集测量路由准确率、证据忠实度、压缩前后成功率和成本变化。
- 执行 300 SSE、70 job/min、16–32 Agent Run 容量压测和 SLO 校准。
- E7 多模型健康/成本路由、E8 文档发布运营接口、E10 灰度发布平台、E11 运营控制台仍是后续产品迭代，不应标记为已完成。
