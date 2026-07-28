# Agentic RAG 面试型源码拆解课程

这套文档的目标不是包装项目，而是把 SubjectsAgent 的真实实现拆成可以面试表达的层级。

组织方式参考 `learn-claude-code`：先从最小 Agent Loop 开始，每一篇只加一个 Harness 机制，最后组合成完整 Agentic RAG 系统。区别是这里所有内容都绑定本项目源码，不讲不存在的能力。

## 阅读顺序

第一遍只看每篇的“这一层解决什么问题”和 Mermaid 图，建立整体链路。

第二遍看“我们项目里的真实源码”，把概念映射到代码。

第三遍重点看“面试官可能怎么问”，按 30 秒、2 分钟、源码级追问三层回答训练。

## 第一批：Agent Loop + Tool Use

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

## 源码总入口

- Agent 主循环：`agent_runtime/src/tool_system/agent_loop.py`
- 工具注册：`agent_runtime/src/tool_system/registry.py`
- 默认工具：`agent_runtime/src/tool_system/defaults.py`
- 工具协议：`agent_runtime/src/tool_system/protocol.py`
- 前置检查：`agent_runtime/src/tool_system/preflight.py`
- 工具调度：`agent_runtime/src/tool_system/scheduler.py`
- 运行状态：`agent_runtime/src/tool_system/run_state.py`
- 运行预算：`agent_runtime/src/tool_system/run_budget.py`
- 任务契约：`agent_runtime/src/tool_system/task_contract.py`
- 后端作业：`backend/app/services/agent_jobs.py`

