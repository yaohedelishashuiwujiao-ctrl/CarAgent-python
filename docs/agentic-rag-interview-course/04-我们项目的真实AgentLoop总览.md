# 04-我们项目的真实 Agent Loop 总览

## 这一层解决什么问题

最小 Tool Use Loop 只讲“模型调用工具”。真实项目还需要路由、权限、上下文、证据、状态、并发、完成判断。

这一篇把真实 `run_agent_loop()` 放到一张图里。

更准确地说，这一篇讲的是：一个 Agent Loop 怎么从“模型自由发挥”变成“可控闭环”。

在汽车 Agent 场景里，一次用户请求可能是：

- 查一个确定性参数，比如“理想 L9 轴距是多少”。
- 查手册知识，比如“NOA 使用限制是什么”。
- 做分析，比如“对比三款 MPV 的空间和底盘方案”。
- 生成产物，比如“做 6 页 PPT”。

这些任务如果都用同一个 `prompt -> LLM -> answer`，会出现四类问题：

| 问题 | 结果 |
|---|---|
| 工具选择不可控 | 简单参数题也可能走大模型长推理，复杂任务又可能漏查数据 |
| 执行边界不可控 | 模型可能重复调用同一工具、越权查数据、无限试错 |
| 证据不可控 | 回答看起来合理，但不知道来自 SQL、RAG 还是模型编造 |
| 完成判断不可控 | 用户要 PPT，模型可能只返回“已完成 PPT 内容” |

所以真实 Agent Loop 不是“while 循环调用模型”，而是一个带路由、工具、证据、状态和验收门的控制算法。

## 最小模式

```mermaid
flowchart TD
    U[用户请求] --> R[decide_route]
    R --> INIT[初始化 RequirementState / RunState / RunBudget]
    INIT --> PROMPT[组装 System Prompt<br/>route policy + citation policy + execution policy]
    PROMPT --> TOOLSET[选择 active tool schemas]
    TOOLSET --> LLM[调用模型]
    LLM --> D{有 tool_use?}
    D -- no --> GATE{任务契约满足?}
    GATE -- yes --> FINAL[引用修复 + 最终回答]
    GATE -- no --> REMIND[contract reminder / completion recovery]
    REMIND --> LLM
    D -- yes --> CHECK[Preflight / 去重 / 熔断]
    CHECK --> EXEC[ToolRegistry / ToolCallScheduler 执行]
    EXEC --> OBS[ToolResult observation]
    OBS --> EVID[注册 evidence / citation]
    EVID --> STATE[更新 RunState / RunBudget / RequirementState]
    STATE --> TOOLSET
```

## 加上这一层后 Loop 怎么变化

真实 Agent Loop 不是裸循环，而是多了 5 个控制面：

| 控制面 | 作用 |
|---|---|
| Route | 判断任务类型，决定工具候选集、预算、模型 tier |
| Context | 组装 system prompt、历史、工具 schema、运行状态 |
| Safety | preflight、权限、数据边界、去重、熔断 |
| State | 记录计划、证据、失败路径、进展 |
| Completion | 判断是否满足 task/output contract |

可以把它理解成一个工程化算法：

```text
输入：用户请求、历史上下文、可用工具、权限和数据范围
状态：RouteDecision、RunState、RunBudget、TaskRequirementState、EvidenceRegistry
循环：
  1. 根据 route 和状态组装 prompt 与工具 schema
  2. 调用模型，让模型选择回答或 tool_use
  3. 如果有 tool_use，Runtime 先做合法性检查和调度
  4. 工具结果回来后，更新证据、进展、失败路径和预算
  5. 如果模型想结束，Runtime 检查 contract 是否满足
退出：contract 满足，或者预算/失败策略判断无法继续
```

这个算法的核心不是“模型越来越聪明”，而是每轮都把模型输出放回 Runtime 控制之下。

## 我们项目里的真实源码

主入口：

- `agent_runtime/src/tool_system/agent_loop.py`

关键调用：

- `decide_route()`：来自 `agent_runtime/src/routing_decision.py`
- `TaskRequirementState.from_user_request()`：来自 `task_contract.py`
- `AgentRunState.from_requirements()`：来自 `run_state.py`
- `RunBudget.from_env()`：来自 `run_budget.py`
- `ToolCallScheduler.execute()`：来自 `scheduler.py`

## 关键参数 / 数据结构

| 参数 | 默认值 | 说明 |
|---|---:|---|
| `max_turns` | 后端默认 `24` | Agent 最大模型回合 |
| `CLAWD_TOOL_EXECUTION_FUSE` | `24` | 单次 run 工具执行保险丝 |
| `CLAWD_MAX_PARALLEL_TOOLS` | `4` | 单个模型回合最多并行工具数，最大 16 |
| `RunBudget.max_low_yield_actions` | `3` | 连续低收益工具动作阈值 |

## 面试官可能怎么问

### 问：你们 Agent Loop 里最关键的工程点是什么？

30 秒回答：

> 关键是 Runtime 不只负责调用模型，还负责工具候选集、preflight、工具调度、状态更新、证据注册和完成判断。模型负责决策，Runtime 负责边界和可观测执行。

2 分钟展开：

> 每轮开始时 Runtime 根据 route policy 暴露工具 schema，模型返回 tool_use 后，Runtime 会先做 preflight 和去重，再判断是否可以并行执行。工具结果回来后，会注册 citation、更新 requirement state、run state 和 budget。如果模型直接回答，Runtime 还要检查 output contract，没有满足就提醒模型继续调用工具或进入 fallback。

源码级追问：

> 这些都在 `run_agent_loop()` 里。`tool_uses` 分支处理工具调用；无 tool_use 分支走 completion gate；工具结果后处理会调用 `register_evidence()`、`requirement_state.update_from_tool_result()`、`run_state.record_action()` 和 `run_budget.record_tool_result()`。

## 如果继续追问到细节

可以说：

- 对 artifact 任务，不能只回答文字，必须生成实际文件。
- 对结构化数据任务，SQL citation 优先于知识库 citation。
- 对重复工具调用，Runtime 用 fingerprint 拒绝。

还可以补一句工程取舍：

> 我们没有把业务流程完全写死成 DAG，因为汽车问答、数据分析和产物生成差异很大；但也没有让模型完全自由调用工具。做法是中间路线：模型负责选择下一步，Runtime 负责边界、调度、证据和完成验收。

## 本层小结

我们的 Agent Loop 是“模型决策 + Harness 执行控制”的闭环，而不是简单的 RAG Chain。
