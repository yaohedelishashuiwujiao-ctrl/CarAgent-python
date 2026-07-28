# 生产级数据分析 Agent：工具能力、路由与执行控制设计

更新日期：2026-07-16  
状态：设计基线（Phase 0 完成，Phase 1 与渐进式 Tool Discovery 首版已落地）  
适用范围：自研 Agent Runtime 的工具发现、选择、预检、执行、结果控制和数据分析工具体系

## 1. 设计结论

本次设计不再把“最大工具调用次数”作为正常的路由和终止机制。目标结构是：

1. 用任务契约明确用户真正要求的结果。
2. 在模型调用前，只发现和暴露当前可能完成任务的工具。
3. 在执行前，用确定性 Preflight 拦截注定失败、越权、不可用或不满足前置条件的调用。
4. 将工具结果归类为稳定的机器状态，而不是让模型从任意错误字符串中猜测下一步。
5. 用需求完成状态和输出契约决定继续或结束。
6. 最大时长、Token、成本和轮次只作为安全熔断器，不限制正常有效调用数量。

目标调用链：

```text
User Request
    ↓
Task Contract / Requirement State / Output Contract
    ↓
Capability Namespace Selection
    ↓
Tool Discovery（精确能力匹配 + 语义检索）
    ↓
Deterministic Eligibility Filter
    ├── permission
    ├── environment
    ├── dependency health
    ├── data coverage
    └── risk policy
    ↓
LLM chooses from the small eligible set
    ↓
Unified Preflight（参数级可执行性判断）
    ↓
Execution Scheduler（并行、超时、重试、取消、资源池）
    ↓
Normalized Tool Outcome + Evidence
    ↓
Requirement Controller
    ├── all required outputs satisfied → COMPLETE
    ├── eligible next action exists → CONTINUE
    ├── approval/clarification required → WAITING_HUMAN
    ├── no capability/data path exists → BLOCKED_WITH_BOUNDARY
    └── safety fuse reached → DEGRADED_FINAL
```

该结构沿用公开成熟机制，而不是另造一套只依赖关键词和次数的算法：

- OpenAI：动态 `is_enabled`、namespace、Tool Search、deferred loading、结果驱动的 tool-use behavior。
- Anthropic：Tool Search、`defer_loading`、strict schema、input examples、程序化批量工具调用。
- Microsoft Semantic Kernel：Contextual Function Selection 与执行前后 Invocation Filters。
- AWS AgentCore：Registry/Gateway 工具发现、每次调用拦截、参数级策略判断。
- Fabric/Snowflake/Databricks/Looker：受治理数据源、专用分析执行器、语义层、示例查询/可信资产与持续评测。

## 2. 实施前真实基线

本章保留 2026-07-15 的实施前审计事实，用来解释设计来源；Phase 0/Phase 1 的已落地差异以
[`PRODUCTION_AGENT_IMPLEMENTATION_STATUS.md`](./PRODUCTION_AGENT_IMPLEMENTATION_STATUS.md) 为准。

### 2.1 在线执行链

真实在线主链为：

```text
Frontend
  → Backend Agent Job
  → Worker
  → agent_runtime/web_app.py
  → ClawdREPL
  → run_agent_loop
  → Provider + ToolRegistry + ToolContext
```

因此设计必须落在 `agent_runtime/src/tool_system` 的真实执行链中，不能只修改 Backend 的简化 Runtime Service。

### 2.2 当前路由

当前 `agent_loop.py` 使用四张关键词 Route Card：

- `artifact_generation`
- `vehicle_spec`
- `manual_qa`
- `trend_analysis`

路由只根据字符串信号加权选一张卡，再用静态 allowlist 过滤 Tool Schema。它没有读取：

- 工具依赖是否健康；
- 数据源是否真实覆盖目标实体、字段、时间和地区；
- 当前用户权限是否使工具仍然可行；
- 工具参数是否违反域名、表、字段、文件或资源限制；
- 之前调用失败后，该能力是否应从候选集中移除；
- 用户要求的制品是否已经真正生成并通过检查。

### 2.3 当前执行控制

当前 Loop 存在以下控制：

- `CLAWD_MAX_TOOL_CALLS_PER_RUN=6` 固定调用预算；
- 最大轮次默认 12；
- 相同工具+参数指纹去重；
- 相同结果去重；
- WebSearch/WebFetch 专用失败计数；
- 若低收益或达到预算，强制无工具综合。

这些适合做安全保护，但不能承担生产级路由职责。固定 6 次会直接伤害需要多源检索、数据校验、图表和 PPT 的正常任务。

### 2.4 ToolRegistry 当前能力

已有能力：

- 工具注册和别名；
- JSON Schema 输入验证；
- Job 工具 allowlist；
- permission allow/deny/ask；
- 审计事件；
- 工具只读、破坏性、strict 等基础元数据。

主要缺口：

- 没有 capability namespace 和结构化边界；
- 没有动态 `is_enabled`/eligibility；
- 没有统一 Preflight；
- 没有依赖健康和数据覆盖探测；
- 没有统一超时、取消、重试、缓存、熔断和资源池；
- 没有结构化结果状态；
- `max_result_size_chars` 已声明但 Registry 未执行；
- 工具调用仍逐个同步执行，无法安全并行独立只读调用。

## 3. 现有生产工具逐项审计

生产 Profile 当前共 11 个工具。

| 工具 | 当前可完成 | 结论 | 主要问题 |
|---|---|---|---|
| `SendUserMessage` | 返回文本和附件 | 支撑工具 | 不验证任务要求的附件是否齐全、正确、可打开 |
| `KnowledgeSearch` | 平台 RAG/RAGFlow 检索 | 部分满足 | “ready” 不代表索引健康或有相关数据；缺少 coverage、freshness、解析质量状态 |
| `KnowledgeFetch` | 获取指定 chunk/document | 部分满足 | 依赖模型先得到合法 ID；无统一文档版本和内容大小治理 |
| `WebFetch` | 获取白名单 HTTPS 页面 | 严重受限 | 默认只允许 `autohome.com.cn`，但 ToolSpec 没向模型声明；不能承担开放行业调研 |
| `SubjectsAttributeLookup` | 车型+属性模糊查询 | 部分满足 | 只支持 vehicle；不支持 component、system profile、证据、媒体、地区/时间等业务实体 |
| `SubjectsSqlSchema` | 查看 MySQL schema | 技术可用，业务不足 | 暴露物理 schema，不是语义层；容易诱发探索调用；不能回答业务指标含义、口径和质量 |
| `SubjectsSqlGlob` | LIKE 搜索表/字段 | 技术可用，业务不足 | 只能做名称发现，无法判断数据覆盖率、更新时间、质量和推荐 join path |
| `SubjectsSqlQuery` | 受控只读 SQL | 部分满足 | 当前默认仅允许 4 张表；无法覆盖系统、零件、媒体、证据等完整模型；没有可信查询/指标层 |
| `AutoChartGenerate` | 9 类基础 PNG 图表 | 部分满足且有 P0 风险 | `sql_query` 直接走 `_query`，未复用 SQL AST、表白名单、data scope、EXPLAIN 和证据快照；图表没有数据血缘 |
| `AutoPptxGenerate` | 1–20 页确定性 PPT | 部分满足 | 只支持单图/小表模板；没有内容/引用/页数之外的 Output Contract 校验；无 evidence manifest |
| `StructuredOutput` | 任意 JSON | 名义满足 | schema 允许任意字段，无法保证调用方要求的结构，也不能作为真正 typed output contract |

### 3.1 已确认的 P0 一致性问题

1. **Chart SQL 绕过统一 SQL 安全链**  
   `AutoChartGenerate` 的 `sql_query` 只走正则只读判断，随后直接 `_query`。它没有调用 `SubjectsSqlQuery` 的 AST、allowed tables、data scope、EXPLAIN 成本门禁和 evidence hash。

2. **工具描述与真实边界不一致**  
   `WebFetch` 描述只是 “Fetch a URL”，真实默认边界却只有 AutoHome 域名。模型无法在选择前知道调用必定被拒绝。

3. **声明的输出上限没有执行**  
   `ToolSpec.max_result_size_chars` 没有在 `ToolRegistry.dispatch` 中裁剪、外置或拒绝大结果。

4. **资源池没有接入 Runtime 工具执行**  
   Backend 声明 model/sql/tool/artifact semaphore，但实际执行链只有 model semaphore 被使用；工具在独立 Runtime 进程同步执行。

5. **没有输出完成契约**  
   Job 可以在未生成用户要求的 PPT/图表/表格时得到一段文字并被标记成功。

## 4. 数据分析全生命周期能力矩阵

评级：满足、部分、缺失。这里评估的是 Agent 可调用能力，而不是 Backend 是否存在某个孤立 API。

| 能力域 | 生产级需要 | 当前评级 | 现有基础/缺口 |
|---|---|---|---|
| 结构化数据查询 | 精确查询、过滤、聚合、join、权限和成本控制 | 部分 | 有 AttributeLookup/SQL；缺语义层、可信查询、完整业务表覆盖 |
| 数据目录与语义发现 | 指标、维度、实体、关系、口径、数据负责人、更新时间 | 缺失 | 只有物理 schema/glob |
| 文档检索 | 多格式解析、ACL、版本、页码/表格定位、质量状态 | 部分 | 有 KnowledgeSearch/Fetch；缺完整 coverage/health/quality contract |
| 公网研究 | 搜索、来源分级、多域 fetch、去重、时效性 | 缺失/严重受限 | 生产无 WebSearch；WebFetch 默认单域 |
| 用户文件分析 | CSV/XLSX/JSON/Parquet/PDF/PPT/DOCX/图片上传与解析 | 缺失 | 生产 Profile 无受控文件摄入工具 |
| API/MCP/外部数据源 | 受治理连接器、凭证、健康、schema、权限 | 缺失 | MCP 只在开发 Profile，且无生产治理层 |
| 数据剖析 | 行数、缺失、唯一值、分布、异常、字段质量 | 缺失 | 无 DataProfile 工具 |
| 清洗与标准化 | 缺失处理、类型转换、去重、单位/币种/时间统一 | 缺失 | 只能让模型写 SQL；无声明式转换和质量报告 |
| 多源融合 | 实体解析、join 规划、冲突处理、来源优先级 | 缺失 | Runtime 无跨 SQL/RAG/Web 的结构化融合工具 |
| 描述性分析 | count、分组、均值、分位数、分布 | 部分 | SQL 和部分图表可做，但无统一分析结果对象 |
| 对比分析 | 多车型/多版本/多地区可比性和单位对齐 | 部分 | SQL 能做基础对比；缺可比性校验和业务口径 |
| 统计分析 | 相关性、假设检验、置信区间、效应量 | 缺失 | 生产无安全统计计算工具 |
| 高级分析 | 回归、聚类、预测、时间序列、敏感性分析 | 缺失 | 无受治理 Python/分析执行器 |
| 文本分析 | 分类、抽取、主题、摘要、表格抽取 | 部分 | 模型+RAG 可摘要；无稳定结构化抽取和验证工具 |
| 图像/底盘视觉 | 检测、分割、区域细化、结果复核 | Agent 侧缺失 | Backend 已有 Vision API，但未注册为 Runtime 工具 |
| 数据质量验证 | schema、范围、完整性、一致性、交叉源冲突 | 缺失 | 只有 SQL 语法/成本和最终 claim 的有限检查 |
| 图表 | 常见图、单位、样本量、可访问性、来源、复现 | 部分 | 9 类 PNG；缺 lineage、chart spec validation、复杂组合图 |
| 表格/数据导出 | CSV/XLSX/JSON/Parquet、字段说明、来源 | 缺失 | Backend 有部分数据集 export，但不是分析 Agent 工具 |
| 报告制品 | PPT/PDF/DOCX/HTML、模板、引用、质量检查 | 部分 | 只有基础 PPT |
| 证据与复现 | 查询快照、数据版本、代码/参数、artifact manifest | 部分 | SQL/RAG 引用已有基础；图表/PPT 没串起血缘 |
| 人机协作 | 澄清、敏感操作审批、暂停/恢复 | 部分 | permission ask 有基础，但生产 Profile 无任务级 clarification contract |
| 运行控制 | 并发、超时、取消、重试、熔断、背压 | 部分 | Job 层有基础；单个工具执行未统一接入 |
| 路由评测 | 候选召回、选择精度、注定失败率、完成率 | 缺失 | 现有 release gate 没覆盖新的路由核心指标 |

### 4.1 当前数据本身的覆盖边界

2026-07-15 对本地 MySQL 的只读核查：

- `vehicle_instance`：3719 行；
- `instance_attribute_value`：383348 行，其中绝大部分是 vehicle 属性；
- `component_instance`：1 行；
- `system_attribute_value`：0 行；
- `evidence_item`：1 行；
- `media_asset`：0 行。

悬架相关字段目前主要覆盖：

- 空气悬架：589 个车型值；
- 前悬架类型：70 个车型值；
- 后悬架类型：70 个车型值；
- 可变悬架功能：17 个车型值；
- 没有检出可支撑“减振器品牌、结构、阀系、阻尼级数、弹簧、衬套、控制器、供应商、硬点”等详细分析的系统化字段。

所以“工具能查数据库”和“系统能完成详细悬架研究”是两回事。路由必须能够报告 `DATA_COVERAGE_INSUFFICIENT`，不能靠重复 SQL/Web 调用掩盖数据缺口。

## 5. 目标核心对象

### 5.1 Task Contract

```python
@dataclass(frozen=True)
class TaskContract:
    task_id: str
    intent: str
    domains: tuple[str, ...]
    entities: tuple[EntityRef, ...]
    measures: tuple[MeasureRef, ...]
    dimensions: tuple[DimensionRef, ...]
    constraints: tuple[Constraint, ...]
    required_evidence: EvidencePolicy
    output_contract: OutputContract
    risk_level: str
```

Task Contract 不要求模型一次解析完所有细节。确定字段从请求/API 直接进入；语义字段可由模型生成，但必须经过 Runtime 校验，并允许随着证据更新。

### 5.2 Requirement State

```python
class RequirementStatus(str, Enum):
    OPEN = "open"
    SATISFIED = "satisfied"
    BLOCKED = "blocked"
    NEEDS_USER = "needs_user"
    NOT_APPLICABLE = "not_applicable"

@dataclass
class Requirement:
    id: str
    kind: str
    description: str
    status: RequirementStatus
    evidence_ids: list[str]
    artifact_ids: list[str]
    blocking_reason: str | None
```

模型不能仅凭自然语言宣布完成。Runtime 根据 ToolOutcome、Evidence 和 Artifact Validator 更新状态。

### 5.3 Output Contract

```python
@dataclass(frozen=True)
class OutputContract:
    response_modes: tuple[str, ...]       # text/table/json/chart/pptx/xlsx/pdf
    required_artifacts: tuple[ArtifactRequirement, ...]
    required_sections: tuple[str, ...]
    exact_counts: Mapping[str, int]       # e.g. {"pptx.slides": 6}
    citation_policy: CitationPolicy
    schema: Mapping[str, Any] | None
    validators: tuple[str, ...]
```

例如“6 页 PPT”只有在以下条件同时成立时才能成功：PPT 文件存在、可打开、恰好 6 页、每页具有要求内容、证据引用满足策略、附件已经返回给用户。

## 6. Tool Capability Contract

扩展 `ToolSpec`，但保留现有 Tool/ToolRegistry 接口，采用兼容迁移。

```python
@dataclass(frozen=True)
class ToolCapability:
    namespace: str                       # data.sql / data.document / analysis.stats / artifact.pptx
    actions: frozenset[str]              # discover/query/profile/transform/analyze/render/export
    entity_types: frozenset[str]
    input_modes: frozenset[str]
    output_modes: frozenset[str]
    limitations: tuple[str, ...]
    positive_examples: tuple[str, ...]
    negative_examples: tuple[str, ...]

@dataclass(frozen=True)
class ToolExecutionPolicy:
    risk: str                            # low/medium/high
    side_effect: str                     # none/artifact/external_write/data_write
    timeout_s: float
    retryable_outcomes: frozenset[str]
    max_attempts: int
    concurrency_pool: str
    supports_parallel: bool
    idempotent: bool
    cache_policy: str

@dataclass(frozen=True)
class ToolDependencies:
    services: tuple[str, ...]
    required_config: tuple[str, ...]
    health_probe: str | None
    coverage_probe: str | None

@dataclass(frozen=True)
class ToolSpec:
    # existing fields retained
    name: str
    description: str
    input_schema: Mapping[str, Any]
    ...
    capability: ToolCapability
    execution: ToolExecutionPolicy
    dependencies: ToolDependencies
    output_schema: Mapping[str, Any]
    result_adapter: str
    preflight_checks: tuple[str, ...]
```

关键原则：

- `description` 不再隐藏真实边界。WebFetch 必须明确当前允许域名或通过动态描述提供。
- 安全限制不能只写在描述里，必须由 Preflight 和 Tool 本身双重执行。
- Tool Search 搜索的是结构化 capability card，不是对名称和描述做简单 substring。
- `negative_examples` 用于明确“不要在什么情况下调用”，但不是安全边界。

## 7. 候选路由设计

### 7.1 不采用手写综合评分公式

首版不使用难以解释的 `0.3*语义相似度 + 0.2*健康度...`。采用可审计的分阶段漏斗：

1. **Namespace Route**：按 Task Contract 选择一个或多个能力域。
2. **Exact Capability Match**：动作、实体、输入和输出类型必须匹配。
3. **Deterministic Exclusion**：权限、环境、健康、覆盖和风险不满足则移除。
4. **Semantic Discovery**：只在剩余同域工具较多时检索最相关 capability card。
5. **LLM Selection**：模型只在小候选集内根据任务和当前 Requirement State 选工具。

Top-K 和候选数量不凭感觉定死，通过路由黄金集选择满足 Recall 的最小值，并按 namespace 单独配置。

### 7.2 Eligibility

```python
class EligibilityStatus(str, Enum):
    ELIGIBLE = "eligible"
    INELIGIBLE = "ineligible"
    NEEDS_APPROVAL = "needs_approval"
    NEEDS_DISCOVERY = "needs_discovery"

@dataclass(frozen=True)
class EligibilityDecision:
    status: EligibilityStatus
    reason_code: str
    user_safe_message: str
    retry_after_s: float | None = None
    alternative_capabilities: tuple[str, ...] = ()
```

典型确定性排除：

- `WebFetch(nio.com)` 在只允许 AutoHome 时：`DOMAIN_NOT_ALLOWED`；
- 查询不存在的字段：`DATA_FIELD_NOT_COVERED`；
- Knowledge 服务配置存在但健康检查失败：`DEPENDENCY_UNHEALTHY`；
- 用户要求 PPT 而 python-pptx 缺失：`DEPENDENCY_MISSING`；
- restrictive data scope 使用通用 SQL：`DATA_SCOPE_REQUIRES_BUSINESS_TOOL`；
- 要求 2026 东南亚车型但数据只有中国市场：`DATA_COVERAGE_INSUFFICIENT`。

## 8. 统一 Preflight

所有工具必须经过同一入口：

```python
preflight = registry.preflight(call, context, task_state)
if preflight.status != ELIGIBLE:
    emit routing_rejection(preflight)
    remove_or_suspend_candidate(call.name, preflight)
    controller.update_from_preflight(preflight)
    # 不计入 executed_tool_calls
else:
    scheduler.execute(call)
```

Preflight 分两层：

### 8.1 暴露前 Preflight

不需要具体参数即可判断：

- 工具是否属于生产 Profile；
- 用户是否拥有工具和数据权限；
- 服务是否健康；
- 依赖包和配置是否存在；
- 数据源是否覆盖任务实体/地区/时间/字段；
- 当前熔断器是否打开。

不满足的工具完全不暴露给模型。

### 8.2 调用前 Preflight

拿到模型参数后判断：

- JSON Schema 和业务约束；
- URL/域名/IP/重定向策略；
- SQL AST、表、字段、函数、scope 和 EXPLAIN；
- 文件路径、类型、大小、数量；
- 数据行数、预计扫描和输出大小；
- 是否需要用户授权；
- 是否和已执行调用等价；
- 是否有更高层、风险更低的确定性工具。

Preflight 通过不替代工具内部校验；两层必须保持纵深防御。

## 9. ToolOutcome 和错误语义

```python
class ToolOutcomeStatus(str, Enum):
    SUCCESS = "success"
    PARTIAL_SUCCESS = "partial_success"
    NO_DATA = "no_data"
    INVALID_INPUT = "invalid_input"
    CAPABILITY_MISMATCH = "capability_mismatch"
    DATA_COVERAGE_INSUFFICIENT = "data_coverage_insufficient"
    PERMISSION_DENIED = "permission_denied"
    APPROVAL_REQUIRED = "approval_required"
    DEPENDENCY_UNHEALTHY = "dependency_unhealthy"
    TRANSIENT_FAILURE = "transient_failure"
    PERMANENT_FAILURE = "permanent_failure"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"

@dataclass(frozen=True)
class ToolOutcome:
    status: ToolOutcomeStatus
    data: Any
    summary: str
    evidence: tuple[EvidenceRef, ...]
    artifacts: tuple[ArtifactRef, ...]
    diagnostics: Mapping[str, Any]
    retryable: bool
    retry_after_s: float | None
    suggested_capabilities: tuple[str, ...]
```

只有 `TRANSIENT_FAILURE` 和明确可恢复的 `DEPENDENCY_UNHEALTHY` 才能自动重试。`INVALID_INPUT`、`CAPABILITY_MISMATCH`、`DATA_COVERAGE_INSUFFICIENT` 和 `PERMISSION_DENIED` 不允许原样重试。

## 10. 执行与并发策略

### 10.1 并行条件

只有同时满足以下条件的调用才能并行：

- 都是只读或幂等；
- 没有输入依赖；
- 不写同一个 artifact/session state；
- 各自资源池仍有配额；
- 总结果大小可以进入上下文或外置存储。

典型可并行：两个独立知识检索、多个车型的确定性属性查询。  
典型不可并行：先查 schema 再写 SQL；先生成图表再生成 PPT；会修改同一文件的两个制品调用。

### 10.2 资源池

Runtime 执行器按 capability 进入真实资源池：

- `model`
- `sql`
- `knowledge`
- `web`
- `compute`
- `artifact`
- `vision`
- `external_action`

Backend Job semaphore 不能代替 Runtime 工具资源池。跨实例时需通过 Redis/等价租约实现全局配额。

### 10.3 程序化分析执行

针对多次同构查询、批量过滤和统计计算，引入受控 `AnalysisCompute` 执行器，而不是让模型每处理一行都产生一次 tool round-trip。执行器只能调用批准的数据句柄和纯函数分析库，不能等同于开放 Bash。

## 11. 数据分析工具目标体系

工具应按 namespace 组织，每组保持小而清晰。

### 11.1 P0：补齐安全路由与基础闭环

| Namespace | 目标工具 | 说明 |
|---|---|---|
| `data.catalog` | `DataCatalogSearch` | 查实体、字段、指标、关系、口径、更新时间、覆盖率，不直接暴露整库 schema |
| `data.vehicle` | 扩展 `SubjectsAttributeLookup` | 支持 vehicle/system/component，批量实体和批量属性 |
| `data.sql` | `SubjectsSqlQuery` | 保留受控 SQL，成为所有 SQL 型工具唯一执行内核 |
| `data.profile` | `DataProfile` | 缺失、唯一值、分位数、分布、异常和质量报告 |
| `analysis.basic` | `DataTransform`、`DescriptiveAnalysis` | 声明式过滤、join、派生列、单位标准化、描述性统计 |
| `artifact.chart` | 重构 `AutoChartGenerate` | 只接受 data handle/query result，禁止自行绕过 SQL 门禁 |
| `artifact.pptx` | 扩展 `AutoPptxGenerate` | 接受 evidence/artifact refs，输出 manifest 并执行 deck validator |
| `artifact.export` | `TableExport` | CSV/XLSX/JSON，带字段说明、单位、来源和数据时间 |

### 11.2 P1：完整分析能力

| Namespace | 目标工具 | 说明 |
|---|---|---|
| `data.file` | `TabularFileInspect`、`DocumentIngest` | 受控分析用户上传文件，文件进入 Job sandbox/object storage |
| `data.web` | `WebSearch`、`WebFetch` | 来源策略、域名集合、时效、去重、失败分类动态可见 |
| `analysis.stats` | `StatisticalAnalysis` | 相关、检验、置信区间、效应量，输出方法和假设 |
| `analysis.timeseries` | `TimeSeriesAnalysis` | 趋势、季节性、变化点、预测与区间 |
| `analysis.model` | `ModelAnalysis` | 回归、聚类、敏感性；必须有数据规模和可解释性约束 |
| `analysis.text` | `StructuredExtraction` | 文档表格/字段抽取，输出 schema、置信度和来源位置 |
| `analysis.vision` | `ChassisVisionAnalyze` | 封装现有 Backend Vision 服务，并声明模型健康/版本/置信度 |
| `artifact.report` | `ReportGenerate` | PDF/DOCX/HTML 模板化输出与引用检查 |

### 11.3 P2：可信资产与语义分析

- Semantic Metric / Business Measure Registry；
- Verified Query / Trusted Asset Registry；
- 实体解析和多源冲突检测；
- 数据质量规则库；
- 按真实使用反馈自动生成但需人工审核的路由评测案例；
- Connector/MCP Registry 和生产审批发布流程。

## 12. Requirement Controller 状态机

```text
PLANNING
  → DISCOVERING_TOOLS
  → READY_TO_EXECUTE
  → EXECUTING
  → EVALUATING_RESULT
       ├── requirement satisfied → READY_TO_EXECUTE / VALIDATING_OUTPUT
       ├── alternative eligible → READY_TO_EXECUTE
       ├── approval needed → WAITING_HUMAN
       ├── no capability/data → BLOCKED_WITH_BOUNDARY
       └── transient failure → RETRY_WAIT
  → VALIDATING_OUTPUT
       ├── contract valid → COMPLETE
       └── repair action eligible → READY_TO_EXECUTE
```

终止原因必须结构化记录：

- `OUTPUT_CONTRACT_SATISFIED`
- `NO_ELIGIBLE_CAPABILITY`
- `DATA_COVERAGE_INSUFFICIENT`
- `WAITING_FOR_APPROVAL`
- `USER_CANCELLED`
- `SAFETY_DEADLINE_EXCEEDED`
- `SAFETY_COST_EXCEEDED`
- `SAFETY_MAX_TURNS_EXCEEDED`

“模型没有再返回 tool call”不等于任务完成。如果 Output Contract 未满足，Controller 应要求修复、选择替代工具或明确失败边界。

## 13. 评测与生产指标

### 13.1 离线路由集

每条用例至少标注：

- 所需 capability；
- 可接受工具集合；
- 禁止工具集合；
- 必须被 Preflight 拦截的调用；
- 预期输出契约；
- 数据覆盖不足时的正确边界。

### 13.2 核心指标

| 指标 | 含义 |
|---|---|
| `capability_recall_at_k` | 正确能力是否进入候选集 |
| `tool_selection_precision` | 实际执行的工具中有多少属于正确路径 |
| `doomed_call_execution_rate` | 已知必败调用仍进入真实执行的比例，目标趋近 0 |
| `preflight_true_rejection_rate` | Preflight 对必败/越权调用的正确拒绝率 |
| `productive_call_ratio` | 使 Requirement 状态前进的执行调用占比 |
| `redundant_call_rate` | 重复或无新增证据调用比例 |
| `output_contract_success_rate` | 任务结束时输出契约真实满足比例 |
| `boundary_accuracy` | 能力/数据不足时是否正确停止并解释边界 |
| `transient_retry_success_rate` | 可重试错误经过受控重试后的恢复率 |
| `tool_latency/cost/error by capability` | 按能力域观察，而非只按工具名 |

固定最大调用次数不作为质量指标。需要观察的是有效调用、无效调用和完成契约。

## 14. 分阶段实施顺序

### Phase 0：安全修正与可观测基线

1. 修复 Chart SQL 绕过，所有 SQL 统一走受控执行内核。
2. 执行 `max_result_size_chars` 或将大结果外置为 data handle。
3. 引入结构化 ToolOutcome 和 reason code。
4. 为现有 11 个生产工具补全 capability/limitations/dependencies。
5. 记录 candidate set、排除原因、Preflight 和 Requirement 变化事件。

### Phase 1：路由与终止主链

1. Task Contract、Requirement State、Output Contract。
2. Namespace + exact capability + deterministic eligibility。
3. 结构化 capability Tool Search。
4. 统一 Preflight。
5. Requirement Controller 接管继续/结束判断。
6. 固定 6 次改为可配置安全熔断；正常策略不依赖它。

### Phase 2：基础数据分析工具闭环

1. DataCatalogSearch 和数据覆盖探测。
2. Vehicle/System/Component 业务查询工具。
3. DataProfile、DataTransform、DescriptiveAnalysis。
4. Chart 只消费受控 data handle。
5. TableExport 和带证据 manifest 的 PPT。

### Phase 3：高级分析与连接器

1. 上传文件分析。
2. 统计/时间序列/模型分析执行器。
3. Vision 工具化。
4. Web 与 MCP 生产 Registry/Gateway。
5. Verified Query/Trusted Asset 与语义指标层。

## 15. 首批验收用例

1. **禁止域名**：模型想调用 `WebFetch(nio.com)`，必须在真实网络调用前得到 `DOMAIN_NOT_ALLOWED`，并从候选集中移除。
2. **不存在字段**：任务要求减振器阀系但 Catalog 无覆盖，必须得到 `DATA_FIELD_NOT_COVERED`，不能循环查 schema/SQL。
3. **6 页 PPT**：任务只有在 PPT 可打开、恰好 6 页、附件返回、各页内容和证据要求通过时成功。
4. **图表 SQL 安全**：Chart 不能查询 SubjectsSqlQuery 无权访问的表，也不能绕过 restrictive data scope。
5. **多车型独立查询**：允许并行执行，结果归并后一次进入模型上下文。
6. **SQL 暂时超时**：按 `TRANSIENT_FAILURE` 有界重试；语法错误不得重试。
7. **知识库空覆盖**：健康但无目标车型文档时返回 `DATA_COVERAGE_INSUFFICIENT`，不能当作网络故障重复检索。
8. **文本提前结束**：模型在 PPT 尚未生成时直接给文本，Output Contract 阻止 Job 标记成功。
9. **大结果**：超过结果阈值时外置为 data handle，只把 schema、统计摘要和引用放进上下文。
10. **全部需求满足**：即使尚未达到任何调用预算，也立即停止工具调用并生成最终结果。

## 16. 明确不采用的做法

- 不再用固定工具调用次数指导正常任务。
- 不把所有工具一次性暴露给模型。
- 不用纯关键词 Route Card 作为最终路由器。
- 不仅靠 ToolSpec 描述约束安全边界。
- 不对所有错误统一重试。
- 不把开放 Bash/Python 当作补齐数据分析能力的捷径。
- 不因 Backend 已有某个 API 就声称 Agent 已具备该能力。
- 不在缺少数据覆盖时让模型通过更多调用“碰运气”。

## 17. 官方机制参考

- OpenAI Agents SDK Tools: https://openai.github.io/openai-agents-python/tools/
- OpenAI Agents SDK Runner: https://openai.github.io/openai-agents-python/running_agents/
- Anthropic Tool Context: https://platform.claude.com/docs/en/agents-and-tools/tool-use/manage-tool-context
- Anthropic Tool Reference: https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-reference
- Anthropic Programmatic Tool Calling: https://platform.claude.com/docs/en/agents-and-tools/tool-use/programmatic-tool-calling
- Microsoft Contextual Function Selection: https://learn.microsoft.com/en-us/semantic-kernel/frameworks/agent/agent-contextual-function-selection
- Microsoft Semantic Kernel Filters: https://learn.microsoft.com/en-us/semantic-kernel/concepts/enterprise-readiness/filters
- Google Gemini Function Calling: https://ai.google.dev/gemini-api/docs/function-calling
- AWS AgentCore: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/
- Microsoft Fabric Data Agent: https://learn.microsoft.com/en-us/fabric/data-science/concept-data-agent
- Snowflake Cortex Agents: https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-agents
- Databricks Genie Concepts: https://docs.databricks.com/aws/en/genie/concepts
- Looker Data Agents: https://docs.cloud.google.com/looker/docs/conversational-analytics-looker-data-agents
