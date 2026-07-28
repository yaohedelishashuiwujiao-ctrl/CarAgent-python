# 文档正确性审计标准

这份课程不是为了堆概念，而是为了面试时能被追问到源码细节。后续每篇文档必须按下面标准验收。

## 正确性分级

| 等级 | 含义 | 能不能直接背 |
| --- | --- | --- |
| A | 已对照源码、关键参数、调用链或测试用例 | 可以作为主回答 |
| B | 源码路径存在，核心概念基本成立，但还缺完整调用链解释 | 可以看，但面试前要复核 |
| C | 更像概念整理，缺少源码锚点或为什么这么做 | 不能直接背 |
| X | 发现与源码不一致 | 必须重写 |

## 每篇必须回答的 6 个问题

1. 这个模块到底是什么，不允许只写英文名词。
2. 为什么项目里需要它，不允许只写“提升效果”“更稳定”。
3. 源码入口在哪，关键类、函数、参数分别是什么。
4. 它在真实 Agent 流程里什么时候触发，前后环节是谁。
5. 面试官追问到源码时，应该怎么解释。
6. 哪些说法不能讲，避免把概念讲过头。

## 写作口径

每篇文档都必须先解释工程问题，再讲设计，再落源码。

不合格写法：

```text
这个模块在 xxx.py，里面有 A 类、B 函数、C 参数。
```

合格写法：

```text
真实 Agent 会遇到什么矛盾？
为什么不能用更简单的方法？
我们采用了什么控制策略？
这个策略在流程里怎么运转？
最后再说明源码在哪里验证。
```

代码是证据，不是主线。主线应该是算法工程考虑：

| 要讲清楚的问题 | 解释重点 |
| --- | --- |
| 为什么需要这个模块 | 它解决了哪类真实失败、成本、并发、准确性或可控性问题 |
| 为什么这样设计 | 相比简单方案，它多控制了什么风险 |
| 流程怎么跑 | 输入是什么、状态怎么变化、什么时候退出 |
| 参数为什么这么设 | 它控制的是成本、召回、延迟、并发还是安全边界 |
| 源码怎么证明 | 只用源码锚定关键事实，不把文档写成文件清单 |

## 审计方法

### 1. 路径存在性

把文档里的源码路径全部抽出来，逐个检查是否存在。

当前检查结果：

```text
docs=27 refs=115 existing=115 missing=0
```

这只能证明“引用的文件存在”，不能证明文档解释完全正确。

### 2. 关键实体校验

对每篇文档中的核心名词，用 `rg` 回到源码确认。

已确认存在的关键实体包括：

| 实体 | 源码位置 |
| --- | --- |
| `RouteDecision` | `agent_runtime/src/routing_decision.py` |
| `estimated_cost` | `agent_runtime/src/routing_decision.py`, `backend/app/services/agent_routing.py`, `backend/app/services/agent_jobs.py` |
| DRR credit | `backend/app/services/agent_jobs.py` |
| `RunBudget` | `agent_runtime/src/tool_system/run_budget.py` |
| `TaskRequirementState` | `agent_runtime/src/tool_system/task_contract.py` |
| `OutputContract` | `agent_runtime/src/tool_system/task_contract.py` |
| `KnowledgeSearch` / `KnowledgeFetch` | `backend/app/services/rag.py`, `agent_runtime/src/tool_system/tools/knowledge.py` |

### 3. 参数来源

参数必须来自代码或配置，不能凭经验写。

例子：

| 参数 | 来源 |
| --- | --- |
| `DEFAULT_DENSE_TOP_K = 50` | `backend/app/services/rag.py` |
| `DEFAULT_SPARSE_TOP_K = 50` | `backend/app/services/rag.py` |
| `DEFAULT_RERANK_TOP_N = 20` | `backend/app/services/rag.py` |
| `RRF_K = 60` | `backend/app/services/rag.py` |
| `AGENT_WORKER_CONCURRENCY = 8` | `backend/app/services/agent_jobs.py` |
| `AGENT_DRR_BASE_QUANTUM = 4` | `backend/app/services/agent_jobs.py` |
| `AGENT_DRR_MAX_CREDIT = 32` | `backend/app/services/agent_jobs.py` |
| `RunBudget.max_low_yield_actions = 3` | `agent_runtime/src/tool_system/run_budget.py` |

### 4. 调用链校验

图里的箭头必须能解释成真实代码调用或数据流。

比如成本控制闭环不是一条虚构链路，而是三层：

| 层 | 真实代码 |
| --- | --- |
| 入队前路由估价 | `estimate_agent_job_route()` |
| 后端队列公平调度 | `AgentJobService` 的 DRR credit 逻辑 |
| 单次运行预算监督 | `RunBudget.record_model_turn()` / `record_tool_result()` / `should_degrade()` |

### 5. 测试或运行证据

能找到测试的章节，必须标出对应测试。

例子：

| 能力 | 测试位置 |
| --- | --- |
| Agent Loop | `agent_runtime/tests/test_agent_loop.py` |
| RunBudget | `agent_runtime/tests/test_run_budget.py` |
| Tool Scheduler | `agent_runtime/tests/test_tool_scheduler.py` |
| Task Router | `agent_runtime/tests/test_task_router.py` |
| Tool System / OutputContract | `agent_runtime/tests/test_tool_system_tools.py` |
| RAG Evaluation | `tests/test_eval_harness.py`, `evals/run_eval.py` |

## 当前 00-25 的状态

| 章节范围 | 状态 | 说明 |
| --- | --- | --- |
| 17 | A | 已重写，解释了 Contract 是什么、为什么存在、源码如何阻止假完成 |
| 25 | A | 已重写，解释了成本控制图、三层闭环、DRR、RunBudget、参数来源 |
| 00-16 | B | 路径存在，但需要按新标准补“为什么”和源码调用链 |
| 18-24 | B | 手写章节，源码锚点存在，但还需要逐篇做参数和测试证据审计 |
| 26+ | 未写 | 不允许批量生成 |

## 后续处理原则

后面不继续堆新章节，先把已有章节逐篇升级到 A。

每篇升级时必须输出：

1. 源码锚点。
2. 真实调用链。
3. 关键参数表。
4. 为什么这样设计。
5. 面试官追问。
6. 不能乱讲的边界。

没有通过这些检查的内容，不应该当成面试答案背。
