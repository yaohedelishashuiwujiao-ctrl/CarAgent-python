# 01-什么是 Agent Harness

## 这一层解决什么问题

Agent 的智能来自模型本身，但模型要在真实业务里工作，必须有一套 Harness 给它提供环境。

在我们项目里，Harness 不是一个抽象词，它具体包括：工具系统、RAG、SQL 数据工具、权限、调度、Memory、上下文压缩、证据闭环和完成判断。

更直白地说，Harness 是“模型和真实业务系统之间的工程外壳”。

模型本身只能生成文本或 tool call 意图，但真实项目要求的是：

| 真实要求 | 只靠模型会怎样 | Harness 负责什么 |
|---|---|---|
| 查汽车手册、论文、维修资料 | 模型可能凭记忆编 | 把 RAG 封装成可调用工具 |
| 查车型参数和结构化数据 | 模型不知道数据库边界 | 暴露 SQL/Attribute 工具并限制 scope |
| 生成图表或 PPT | 模型可能只描述“我生成了” | 执行真实产物生成并校验文件 |
| 多用户同时请求 | 模型没有队列概念 | 后端 job、租约、并发和 DRR 调度 |
| 失败后恢复 | 模型容易重复试错 | 记录失败路径、低收益动作和 fallback |
| 回答可追溯 | 模型可能不给证据 | 注册 evidence、修复 citation、检查 contract |

所以 Harness 的价值不是“多写了一层代码”，而是把不可靠的自然语言推理，放进一个可控、可观测、可验收的执行系统里。

## 最小模式

```mermaid
flowchart LR
    MODEL[LLM<br/>负责推理和决策] --> HARNESS[Harness<br/>负责提供环境和执行能力]
    HARNESS --> TOOLS[Tools]
    HARNESS --> KNOWLEDGE[RAG Knowledge]
    HARNESS --> DATA[SQL / Structured Data]
    HARNESS --> PERM[Permissions]
    HARNESS --> MEMORY[Memory / Context]
    HARNESS --> SCHED[Scheduler]
```

## 加上这一层后 Loop 怎么变化

没有 Harness 时：

```text
User -> LLM -> Answer
```

有 Harness 后：

```text
User -> LLM -> 选择行动 -> Harness 执行动作 -> Observation -> LLM -> Final
```

关键变化是：模型不再只能“说”，它可以通过工具“行动”。

但更重要的变化是：行动不由模型完全自由发挥。

模型决定“下一步想做什么”，Harness 决定：

- 这个工具当前能不能用。
- 参数是否合法。
- 数据范围是否越权。
- 多个工具能不能并行。
- 结果是否算有效证据。
- 用户要求的产物是否真的完成。

这就是为什么面试时不要把 Agent 讲成“LLM 会调用工具”。真正有工程含量的是：Runtime 在模型周围加了控制面。

## 我们项目里的真实源码

| Harness 能力 | 源码 |
|---|---|
| Agent 主循环 | `agent_runtime/src/tool_system/agent_loop.py` |
| 工具注册和分发 | `agent_runtime/src/tool_system/registry.py` |
| 默认工具集合 | `agent_runtime/src/tool_system/defaults.py` |
| 工具执行策略 | `agent_runtime/src/tool_system/execution.py` |
| RAG 知识工具 | `agent_runtime/src/tool_system/tools/knowledge.py` |
| RAG 服务 | `backend/app/services/rag.py` |
| 作业调度 | `backend/app/services/agent_jobs.py` |
| Memory / Context | `agent_runtime/src/context_system/`, `run_state.py` |

## 关键参数 / 数据结构

我们项目可以抽象成：

```text
SubjectsAgent = LLM
              + Agent Loop
              + Tool Registry
              + KnowledgeSearch / KnowledgeFetch
              + Subjects SQL Tools
              + RouteDecision
              + ToolCallScheduler
              + RunState / RunBudget
              + TaskRequirementState
              + Evidence / Citation
```

这里的每一项不是简单模块名，而是一类工程控制：

| 控制对象 | 工程含义 |
|---|---|
| `Tool Registry` | 控制模型能看到哪些能力 |
| `RouteDecision` | 控制当前任务走什么工具策略和预算 |
| `ToolCallScheduler` | 控制工具执行顺序、并发和资源池 |
| `RunState / RunBudget` | 控制是否有进展、是否空转、是否超预算 |
| `TaskRequirementState` | 控制任务是否真的完成 |
| `Evidence / Citation` | 控制回答能不能追溯到数据来源 |

## 面试官可能怎么问

### 问：你们为什么说这是 Agent，而不是普通 RAG？

30 秒回答：

> 普通 RAG 通常是“检索 -> 拼 prompt -> 回答”。我们这里是 Agentic RAG：RAG 作为工具挂在 Agent Loop 上，模型可以决定什么时候查知识库、什么时候查 SQL、什么时候生成图表或 PPT。Runtime 还维护权限、调度、状态、证据和完成判断。

2 分钟展开：

> 我们的核心不是单一检索链，而是 Harness。模型每一轮拿到当前 messages、可用工具 schema、route policy、task requirements 和 run state，然后决定下一步。如果需要知识检索，它调用 `KnowledgeSearch`；如果需要结构化车型参数，它调用 SQL/Attribute 工具；如果要产物，它调用图表或 PPT 工具。工具结果会以 observation 回填给模型，直到 contract 满足才结束。

源码级追问：

> 可以看 `run_agent_loop()`。它先 `decide_route()`，再组装 system prompt、citation policy、execution policy 和 requirement prompt，然后循环调用 provider。模型返回 tool_use 后，Runtime 会 preflight、dispatch、注册 evidence、更新 run state，再把 tool_result 放回消息里。

## 如果继续追问到细节

可以强调：

- Agent Loop 不硬编码业务步骤，复杂任务由模型通过工具和 TodoWrite 推进。
- Harness 控制边界：哪些工具能用、数据范围是什么、失败后能不能重试。
- RAG 是 Knowledge 工具，不是整个系统的唯一能力。

## 本层小结

面试时先讲 Harness，会比直接讲 RAG 更高级，也更符合项目真实复杂度。
