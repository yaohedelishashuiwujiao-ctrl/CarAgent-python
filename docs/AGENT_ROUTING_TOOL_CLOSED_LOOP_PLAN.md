# Agent 路由、成本与工具调用闭环计划

更新日期：2026-07-18  
状态：Phase A/B/C/D/E 首版已启动  
目标范围：`agent_runtime` 主循环、Backend Job 调度、工具注册/执行、评测与发布门禁

## 1. 目标

本计划把三个目标合成一个可运营闭环：

1. 成本控制：用任务路由决定执行路径、模型档位、工具候选和预算，而不是只靠固定最大轮次。
2. 工具效率：把同一依赖层的独立只读工具调用并行执行，并逐步合并同构查询。
3. 工具准确性：用工具能力契约、preflight、证据校验、黄金集评测和线上审计持续修正路由。

最终闭环：

```text
User Request
  -> RouteDecision
  -> BudgetDecision
  -> ModelDecision
  -> ToolCandidateSet
  -> ToolScheduler
  -> Evidence / Outcome / Cost Audit
  -> Eval + Online Metrics
  -> Route Policy Update
```

## 2. 当前基线

已落地：

- L0 零模型成本路由：`agent_runtime/src/task_router.py`
- Harness 工具候选路由：`agent_runtime/src/tool_system/agent_loop.py`
- 工具能力、执行策略、schema、preflight：`agent_runtime/src/tool_system/registry.py`
- 同轮只读工具并行调度：`agent_runtime/src/tool_system/scheduler.py`
- 运行预算与低收益检测：`agent_runtime/src/tool_system/run_budget.py`
- Backend DRR、公平队列、session lock、lease、Redis Stream：`backend/app/services/agent_jobs.py`
- 工具/引用/任务契约审计元数据：Runtime final metadata 和 Backend Job persistence

主要缺口：

- 路由还没有统一产出模型档位和预算策略。
- Backend 的 `estimated_cost` 仍是轻量关键词规则，不能反映真实 token、工具和制品成本。
- Backend 模型 semaphore 包住整个 Runtime proxy job，不是 Runtime 内每次模型调用。
- 工具并行是安全批处理，还不是依赖 DAG 和同构查询合并。
- 路由准确性、工具选择准确性、成本收益缺少黄金集和线上指标闭环。

## 3. 目标架构

### 3.1 RouteDecision

新增统一路由决策对象，作为 Runtime 和 Backend 的共同协议：

```python
RouteDecision(
    route="vehicle_spec",
    confidence=0.88,
    execution_path="deterministic_workflow | agent_loop | clarification",
    model_tier="none | cheap | standard | strong",
    tool_profile="vehicle_spec_primary",
    budget_class="lookup | normal | analysis | artifact",
    max_model_turns=...,
    max_tool_calls=...,
    max_input_tokens=...,
    expected_evidence_kinds=("structured_data",),
    reason_codes=(...),
)
```

要求：

- L0 能完成的查询直接 `model_tier=none`。
- 简单解释、格式化、摘要默认 `cheap`。
- 结构化查询优先 deterministic 或 `standard` + business tools。
- 多源分析、PPT、长报告才允许 `strong`。
- 低置信路由不能直接升级强模型，先用 cheap/standard 做澄清或工具发现。

### 3.2 BudgetDecision

预算不只记录，还要参与控制：

- 每轮模型调用前检查 token 和价格预算。
- 工具连续低收益时执行降级动作：收窄候选、禁止重复工具、请求重规划、综合已有证据。
- 成本达到阈值时不再自动升级强模型，除非任务契约要求制品且证据已充分。
- 每个 final metadata 必须带 `budget_class`、`model_tier`、`actual_cost_units`、`degrade_reason`。

### 3.3 ToolScheduler

短期保留安全并行规则：

- `is_read_only=True`
- `supports_parallel=True`
- `side_effect="none"`
- `idempotent=True`
- preflight 通过

下一阶段增加两类能力：

- 同构查询合并：多个 `SubjectsAttributeLookup` 或多个 RAG 查询可合并为批量工具输入。
- 依赖层调度：模型一次返回的 sibling calls 作为同层；层内并行，层间串行。

### 3.4 Accuracy Loop

每次运行记录：

- `route`
- `candidate_tools`
- `selected_tools`
- `preflight_rejections`
- `tool_outcome_status`
- `evidence_status`
- `task_contract_status`
- `input_tokens/output_tokens`
- `latency_ms`
- `final_cost_units`

离线评测产出：

- `route_accuracy`
- `tool_candidate_recall`
- `tool_selection_precision`
- `unnecessary_tool_call_rate`
- `parallel_batch_rate`
- `cost_per_satisfied_task`
- `task_success_rate`
- `evidence_supported_rate`
- `fallback_expansion_rate`

## 4. 分阶段实施

### Phase A：基线修复与指标补洞

代码落点：

- `agent_runtime/src/tool_system/agent_loop.py`
- `agent_runtime/src/tool_system/run_budget.py`
- `agent_runtime/tests/test_agent_loop.py`
- `evals/suites/*.json`

任务：

- 修复 citation note 在模型观察值压缩后丢失的问题。
- 修复 `SubjectsAttributeLookup` 成功后仍暴露工具导致多余调用的问题。
- 在 final metadata 中补齐 `budget_class/model_tier/degrade_reason` 的占位字段。
- 增加两类 eval：简单结构化查询必须零模型或单工具完成；工具结果足够时不得继续探测。

验收：

- Agent loop 相关单测通过。
- 简单属性查询不再出现第二次 SQL/schema 探测。
- 有证据结果时下一轮模型可见 harness citation ids。

### Phase B：统一 RouteDecision

代码落点：

- `agent_runtime/src/task_router.py`
- `agent_runtime/src/tool_system/agent_loop.py`
- `backend/app/services/agent_jobs.py`
- 新增 `agent_runtime/src/routing_decision.py`

任务：

- 把 L0 router 和 RouteCard router 合并到统一 `RouteDecision`。
- Backend 创建 Job 时保存 route 快照和 budget class。
- Runtime 使用同一个 route snapshot 控制模型、工具候选和预算。
- 保留 route version，final metadata 和 Job 表可追溯。

验收：

- 同一 prompt 在 Backend 和 Runtime 看到相同 route version。
- route 变更不会破坏旧 Job 的可追溯性。
- 黄金集 route accuracy 达到首期阈值。

### Phase C：模型成本路由

代码落点：

- `agent_runtime/src/providers/*`
- `agent_runtime/src/tool_system/agent_loop.py`
- `backend/app/config.py`
- `.env.example`

任务：

- 建立模型档位配置：`cheap/standard/strong`。
- 支持按 route、budget class、健康度、剩余预算选择 provider/model。
- 记录每轮实际 model name、usage、价格估算和降级原因。
- 强模型只在复杂分析、长上下文、制品生成或标准模型失败后使用。

验收：

- 简单查询强模型调用率低于阈值。
- 成本下降不牺牲黄金集成功率。
- provider 失败时能降级或明确失败边界，不进入无限重试。

### Phase D：工具并行与批量化

代码落点：

- `agent_runtime/src/tool_system/scheduler.py`
- `agent_runtime/src/tool_system/tools/subjects_sql.py`
- `agent_runtime/tests/test_tool_scheduler.py`

任务：

- 给工具声明 `batch_key` 和 `merge_strategy`。
- 合并同构 read-only 查询，减少 model-tool round trip。
- 为 SQL/RAG/Web/Artifact 分别统计池等待时间、执行时间和饱和率。
- 对同一依赖层的并行结果保持稳定顺序提交。

验收：

- 并行工具批次有审计事件和耗时指标。
- 同构查询合并后结果语义不变。
- 工具池饱和时快速失败并给出稳定 reason code。

### Phase E：评测与发布门禁

代码落点：

- `evals/`
- `scripts/agent_release_gate.py`
- `docs/PRODUCTION_AGENT_IMPLEMENTATION_STATUS.md`

任务：

- 建立 route/tool/cost/evidence 黄金集。
- 发布门禁加入成本与工具准确性指标。
- 每次 route policy、tool schema、provider、prompt 变更都跑 gate。
- 将线上审计抽样回流为 eval case。

验收：

- 任一安全、越权、任务契约、引用高风险指标失败即阻断发布。
- route/tool 质量下降超过阈值即阻断发布。
- 每次策略升级有 before/after 成本与成功率报告。

## 5. 权限与环境需求

本计划本地代码修复和文档更新不需要额外权限。

后续真实闭环验收需要一次性准备：

- 真实 MySQL 只读账号和 `SUBJECTS_DATABASE_URL`
- Redis 或等价 broker 的 `REDIS_URL`
- 至少两个模型档位的 provider key/base url/model name
- 运行 `database/migrations/20260715_agent_production_foundation.sql` 的数据库迁移权限
- 可执行压测和 release gate 的 CI 权限

运行期必须继续禁用：

- 生产 Bash
- 任意文件写入
- 任意公网 WebFetch
- DDL/DML SQL 权限

## 6. 首批指标阈值

建议首期门禁：

| 指标 | 阈值 |
|---|---:|
| route_accuracy | >= 0.90 |
| tool_candidate_recall | >= 0.95 |
| tool_selection_precision | >= 0.85 |
| unnecessary_tool_call_rate | <= 0.10 |
| evidence_supported_rate | >= 0.90 |
| task_success_rate | >= 0.85 |
| simple_lookup_strong_model_rate | <= 0.02 |
| simple_lookup_avg_model_turns | <= 1.0 |
| repeated_equivalent_tool_call_rate | <= 0.03 |

阈值先按试点日志校准，不能用演示用例替代黄金集。

## 7. 最近执行顺序

1. 已完成：修复当前 agent loop 单测回归。
2. 已完成：增加 `RouteDecision` 数据结构和 route metadata 输出。
3. 已完成：将 Backend `_estimate_cost` 替换为 route/budget class 估算。
4. 已完成首版：接入 `CLAWD_MODEL_TIER_<TIER>_MODEL` 模型档位覆盖配置。
5. 已完成首版：补 eval suite 和 release gate 指标。
6. 已完成首版：工具同构批量化协议、Scheduler 批量分发、`SubjectsAttributeLookup` 批量入口。
7. 下一步：将 `SubjectsAttributeLookup.run_batch` 内部 SQL 合并为真正单查询、依赖层 DAG 调度和真实 MySQL/Redis/model 的端到端压测与成本报告。

## 8. 当前已落地字段

Runtime final metadata 已输出：

- `route_decision`
- `model_tier`
- `budget_class`
- `tool_profile`
- `model_routing`
- `run_budget`
- `tool_scheduler_ledger`

Backend Job 已保存 Runtime 回传的 `route_decision/model_tier/budget_class/model_routing`，创建 Job 时 queued event 同时记录轻量 `route_estimate`。

模型档位配置：

```dotenv
CLAWD_MODEL_TIER_CHEAP_MODEL=<cheap model override>
CLAWD_MODEL_TIER_STANDARD_MODEL=<standard model override>
CLAWD_MODEL_TIER_STRONG_MODEL=<strong model override>
```

未配置时保持 provider 默认模型，不改变现有部署行为。

## 9. 当前已落地工具批量化协议

`ToolExecutionPolicy` 新增：

- `supports_batch`
- `max_batch_size`

Scheduler 行为：

- 同一模型轮次内，如果所有 dispatchable calls 属于同一工具，且工具只读、无副作用、幂等、声明 `supports_batch=True`，则优先走批量分发。
- 不满足批量条件时回退到既有并行/串行路径。
- 批量分发保留模型原始顺序提交结果，并记录：
  - `batch_tool_group_started`
  - `batch_tool_group_completed`
  - `tool_batch_dispatch_started`
  - `tool_batch_dispatch_completed`

当前 `SubjectsAttributeLookup` 已声明批量能力并提供 `run_batch` 入口。首版 `run_batch` 保守复用单次 `run`，目的是先建立协议、审计和测试闭环；下一步再把多个属性/实体 lookup 合并为一次受治理 SQL 查询。
