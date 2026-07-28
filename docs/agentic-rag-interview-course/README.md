# Agentic RAG 面试型源码拆解课程

这套文档按搭积木的顺序拆解 SubjectsAgent：从最小 Agent Loop 开始，一层层加入 Tool Use、权限、状态、任务、上下文、后端作业、Provider 兼容和成本控制。目标是面试时能从概念讲到源码、参数和边界。

## 已完成章节

| 编号 | 文档 | 本层新增机制 |
|---:|---|---|
| 00 | [阅读路线](./00-阅读路线.md) | 如何使用这套材料 |
| 01 | [什么是 Agent Harness](./01-什么是Agent-Harness.md) | Agent = Model + Harness |
| 02 | [最小 Agent Loop](./02-最小AgentLoop.md) | User -> LLM -> Answer |
| 03 | [加入 Tool Use 后的循环](./03-加入ToolUse后的循环.md) | LLM -> tool_use -> tool_result -> LLM |
| 04 | [我们项目的真实 Agent Loop 总览](./04-我们项目的真实AgentLoop总览.md) | 路由、上下文、工具、状态、完成门 |
| 05 | [工具是什么](./05-工具是什么.md) | schema + permission + execution + result |
| 06 | [ToolRegistry 注册与分发](./06-ToolRegistry注册与分发.md) | 工具注册表和 dispatch |
| 07 | [ToolResult 协议](./07-ToolResult协议.md) | outcome_status / reason_code / retryable |
| 08 | [Preflight 工具前置检查](./08-Preflight工具前置检查.md) | 调用前拒绝不合法动作 |
| 09 | [权限和数据边界](./09-权限和数据边界.md) | allowed_tools / data_scope / production profile |
| 10 | [工具去重与 Fingerprint](./10-工具去重与Fingerprint.md) | 防重复调用和重复结果 |
| 11 | [工具失败与熔断](./11-工具失败与熔断.md) | 低收益、连续失败、执行保险丝 |
| 12 | [TodoWrite 计划机制](./12-TodoWrite计划机制.md) | 模型维护计划，Runtime 记录计划 |
| 13 | [RunState 运行状态](./13-RunState运行状态.md) | 进展、失败路径、证据账本 |
| 14 | [TaskRequirementState 完成判断](./14-TaskRequirementState完成判断.md) | 任务不是模型说完就完 |
| 15 | [OutputContract 产物判断](./15-OutputContract产物判断.md) | PPT/Chart 必须真实生成 |
| 16 | [证据注册与 Citation](./16-证据注册与Citation.md) | evidence -> citation -> final repair |
| 17 | [错误恢复和 Fallback](./17-错误恢复和Fallback.md) | primary 到 fallback，contract reminder |
| 18 | [System Prompt 动态组装](./18-SystemPrompt动态组装.md) | Prompt 不是固定文本，而是运行时组装 |
| 19 | [Audit Events 和可观测性](./19-AuditEvents和可观测性.md) | 用事件和 metadata 解释 Agent 黑盒 |
| 20 | [Context Compact 上下文压缩](./20-ContextCompact上下文压缩.md) | 工具结果外置和原子裁剪 |
| 21 | [前端到后端请求链路](./21-前端到后端请求链路.md) | Web -> Job API -> Runtime -> SSE |
| 22 | [Backend Job 生命周期](./22-BackendJob生命周期.md) | queued/admitted/running/final 状态机 |
| 23 | [SSE 流式事件返回](./23-SSE流式事件返回.md) | Last-Event-ID 断线续传 |
| 24 | [Provider 抽象和模型兼容](./24-Provider抽象和模型兼容.md) | 统一不同模型工具调用格式 |
| 25 | [成本控制闭环](./25-成本控制闭环.md) | route cost、budget、tier、RunBudget |

## 后续章节方向

后面会继续认真写，不批量生成：模型路由、并发调度、SQL 工具链、RAG 全流程、Memory、Artifact、部署、真实案例和专项面试追问。

