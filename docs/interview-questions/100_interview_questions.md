# AI 应用开发面试题 100 道（基于 SubjectsAgent Platform 项目）

---

## 一、前端：React + TypeScript（1-25）

### 1. 简单介绍这个 AI Agent 页面是怎么工作的

**答：** 前端用 TypeScript + React + Ant Design 构建，后端是 Python FastAPI，Agent 运行时基于 Clawd Web Agent。用户输入分析需求后，前端通过 POST 请求到 `/api/agent/chat_jobs` 创建任务，后端返回 job_id 和 events_url。前端通过 SSE（Server-Sent Events）长连接实时接收 Agent 执行事件——包括思考阶段、工具调用、文字增量，最终推送完整回答。React 通过 useState 管理状态，每次状态变化自动刷新 UI，实现实时流式展示。

### 2. React 和 TypeScript 的关系是什么

**答：** React 是基于 JavaScript 的 UI 框架，TypeScript 是 JavaScript 的超集，添加了静态类型系统。两者独立但可以配合使用。TypeScript 在编译阶段检查类型错误，编译后生成纯 JavaScript 在浏览器运行。本项目用 `.tsx` 文件，即 React 组件用 TypeScript 编写，所有数据结构（AgentStatus、AgentMessage、AgentTelemetry 等）都有严格类型定义。

### 3. React 组件由哪几部分组成

**答：** 三部分：① 状态（useState 声明的数据，变了触发 UI 刷新）；② 逻辑（函数，负责修改状态、发请求）；③ 渲染（JSX，根据状态描述 UI 结构）。核心思想是"UI = f(状态)"——状态变了，界面自动更新。

### 4. useState 的工作原理是什么

**答：** useState 返回一个状态变量和一个 setter 函数。状态变量是"具备通知功能的变量"——调用 setter 改值后，React 自动重新渲染引用了该状态的组件。不能直接赋值（`x = 2` 不行），必须通过 `setX(2)` 来触发更新。类比 Q_PROPERTY + NOTIFY 信号，但通知机制内置了。

### 5. useEffect 的作用是什么

**答：** useEffect 是 React 的生命周期钩子。空依赖数组 `[]` 表示只在组件挂载时执行一次（类似构造函数初始化）；有依赖项时表示依赖变化时重新执行。本项目中：挂载时加载 Agent 状态和会话列表；sending 为 true 时启动计时器；消息变化时自动滚动到底部。

### 6. 前端 SSE 是怎么建立连接的

**答：** 分两步：① POST `/api/agent/chat_jobs` 创建任务，后端返回 `{ job_id, events_url }`；② 用 `fetch(events_url)` 建立 HTTP 长连接，通过 `response.body.getReader()` 获取 ReadableStream，循环调用 `reader.read()` 阻塞等待每一帧数据。每帧按 `\n\n` 分割，解析出 event_type 和 payload，根据类型更新 React 状态。

### 7. SSE 和 WebSocket 有什么区别

**答：** SSE 是单向的（服务器→浏览器），基于普通 HTTP 协议，自带断线重连和事件 ID 机制，实现简单。WebSocket 是双向的（浏览器↔服务器），需要独立协议，更复杂。Agent 场景只需要服务器推送事件给前端，前端不需要向 SSE 连接写数据，所以 SSE 更合适。

### 8. SSE 连接是开了一个新进程吗

**答：** 不是。SSE 就是一个普通的 HTTP 连接，只是保持不断开。浏览器端就是一个 TCP socket，资源消耗极低（几 KB 缓冲区）。服务器端用 asyncio 异步 IO 处理，也不额外创建进程或线程。

### 9. 前端是怎么处理 SSE 断连的

**答：** 当前实现中，SSE 连接断开后前端会进入 catch 块，检查 `error.name === "AbortError"`（用户主动取消）还是其他错误。如果是网络错误，会显示"发送失败"提示并更新消息状态。后端有 keepalive 机制（15 秒无事件发 `: keepalive\n\n`），防止中间代理超时断连。

### 10. 前端任务状态机有哪些状态

**答：** `idle → loading → queued → running → streaming → completed/incomplete/error/stopped`。每个状态对应不同的 UI 展示：queued 显示"排队中"，running 显示"正在执行"并展示当前工具，streaming 显示"正在生成回答"，completed 显示最终结果，incomplete 表示达到工具轮次上限未生成完整回答。

### 11. AgentPage 里有哪些子组件

**答：** 三个主要子组件：① `CitationList`：引用来源列表，显示 Agent 的证据卡片（来源类型、标题、URL、内容摘要）；② `IncompleteAnswer`：未生成完整回答时的提示组件；③ `MarkdownAnswer`：将 Agent 返回的 Markdown 文本渲染为 HTML，支持标题、列表、表格、代码、链接。

### 12. 前端怎么做 Markdown 渲染的

**答：** `MarkdownAnswer` 组件用纯 JS 实现了 Markdown 解析器：按行扫描，识别标题（`#`）、列表（`-`/`1.`）、表格（`|`）、分隔线（`---`），分别渲染为对应的 HTML 元素。行内格式（`**加粗**`、`` `代码` ``、`[链接](url)`）通过正则匹配替换为 React 元素。

### 13. 前端怎么保证引用不重复

**答：** `dedupeCitations` 函数通过 `citationKey` 生成每个引用的唯一标识（优先用 citation_id，其次 chunk_id、url、source、title），用 Set 去重。合并消息时也会自动去重，避免连续 assistant 消息带来重复引用。

### 14. 前端怎么实现"逐字显示"效果

**答：** 后端每生成一小段文字就推送一个 `text_delta` 事件。前端收到后把新文字追加到 `assistantText` 变量，然后调用 `patchCurrentAssistant(assistantText)` 更新 React 状态。React 检测到状态变化，自动重新渲染对话区，用户看到文字一个个冒出来。

### 15. 前端怎么处理"达到工具轮次上限"的情况

**答：** 通过 `isMaxTurnsReached(telemetry)` 检查 `stats.max_turns_reached === true`，以及 `isContractIncomplete(telemetry)` 检查 `output_contract_status === "unmet"`。如果触发，taskStatus 设为 `incomplete`，显示特殊提示文案"任务达到工具调用轮次上限，未能生成完整回答"，并保留已有的工具过程和来源供用户复核。

### 16. 前端怎么实现取消任务功能

**答：** 用户点"停止"按钮 → 调用 `stopMessage()` → POST `/api/agent/chat_jobs/{job_id}/cancel` 通知后端 → 同时调用 `abortRef.current.abort()` 中断 SSE 连接 → 前端状态立即切到 `stopped`，显示"正在取消本次任务"。

### 17. React 的组件间通信是怎么实现的

**答：** 在 AgentPage 中没有跨组件通信的复杂需求，所有数据都在主组件内通过 useState 管理。子组件（CitationList、MarkdownAnswer）通过 props 接收数据，是单向数据流。如果需要跨组件通信，React 通常用 props 传递或 Context API。

### 18. 前端 API 层是怎么封装的

**答：** `api.ts` 封装了三个核心函数：`apiUrl(path)` 拼接完整 URL（支持环境变量配置后端地址）；`apiHeaders(json?)` 构造请求头（Content-Type + Bearer Token 鉴权，Token 存在 localStorage 中）；`fetchJson<T>(url)` / `postJson<T>(url, body)` 发请求并自动处理错误。

### 19. 前端跨域问题是怎么解决的

**答：** 开发时前端 Vite 跑在 5173 端口，后端 FastAPI 跑在 8000 端口。后端通过 `CORSMiddleware` 配置允许的 origin、methods、headers 来解决。生产环境通常用 Nginx 反向代理，把前后端统一到同域。

### 20. Vite 在这个项目里起什么作用

**答：** Vite 是前端构建工具，负责：① 开发时提供热更新服务器（HMR），改代码后浏览器自动刷新；② 把 TypeScript/JSX 编译成浏览器能执行的 JavaScript；③ 打包所有模块为一个或多个 JS 文件用于生产部署。类比 C++ 项目的 cmake + 编译器。

### 21. localStorage 在这个项目里存了什么

**答：** 存了 `platform_agent_session_id`，即当前激活的会话 ID。这样用户刷新页面后还能回到上次的会话，不需要重新选择。

### 22. 前端怎么判断 API Key 是否加载

**答：** 页面加载时调用 `GET /api/agent/status`，后端返回 `{ current: { has_api_key: boolean } }`。前端用 `<Tag color={has_api_key ? "green" : "red"}>` 显示绿色"API Key 已加载"或红色"缺少 API Key"。

### 23. 前端怎么实现任务洞察面板的三个 Tab

**答：** 用 `observeTab` 状态变量（类型 `ObserveTab = "overview" | "sources" | "process"`），点击 Tab 按钮时 `setObserveTab(tab)` 切换。渲染时用条件判断 `observeTab === "overview"` 显示概览（工具统计、运行预算），`"sources"` 显示引用来源，`"process"` 显示事件时间线。

### 24. 前端"证据质量"是怎么评估的

**答：** `summarizeTelemetry` 函数根据多个指标综合判断：如果 `max_turns_reached` 为 true 则"未完成"；如果有工具失败或大量搜索未命中则"需复核"；如果有多个 URL 来源或成功抓取则"较高"；如果有工具结果则"中等"；否则"待确认"。

### 25. 如果让你优化 Agent 页面的用户体验，你会怎么做

**答：** 四个方面：① 实时状态反馈——通过 SSE 流式推送各阶段，让用户看到"正在思考"/"正在调用工具"；② 等待焦虑缓解——超过 15 秒显示超时提示和计时器；③ 可控性——用户随时可以点"停止"取消任务；④ 结果可追溯——右侧面板展示完整工具调用链路和引用来源。

---

## 二、后端：FastAPI + 任务调度（26-55）

### 26. FastAPI 在这个项目里起什么作用

**答：** FastAPI 是整个后端的业务中枢，不只是路由转发。它负责：① HTTP 入口（接收请求、返回响应）；② 请求验证（校验参数格式）；③ 鉴权（用户身份、权限判断）；④ 业务编排（协调 JobService、Agent Runtime）；⑤ 任务调度（DRR 排队、Worker 分配、并发控制）；⑥ SSE 流式网关（长连接、事件分发）；⑦ 持久化管理（异步写入 MySQL）。

### 27. FastAPI 的路由是怎么定义的

**答：** 用装饰器定义。`@router.post("/chat_jobs")` 处理创建任务，`@router.get("/chat_jobs/{job_id}/events")` 处理 SSE 事件流，`@router.post("/chat_jobs/{job_id}/cancel")` 处理取消。每个路由函数接收请求参数，执行业务逻辑，返回 JSON 或 StreamingResponse。

### 28. 创建任务的 API 返回什么

**答：** POST `/api/agent/chat_jobs` 返回 202 Accepted，body 包含 `{ job_id, status, queue_key, estimated_cost, status_url, events_url }`。202 表示任务已接受但尚未完成。

### 29. 后端任务状态机有哪些状态

**答：** `QUEUED → ADMITTED → RUNNING → SUCCEEDED/FAILED/CANCELLED/REJECTED`。还有 `CANCEL_REQUESTED` 作为中间态。状态转换通过 `ensure_job_transition` 严格校验，不允许非法跳转。

### 30. 后端怎么实现并发的

**答：** 三层配合：① asyncio 协程——单线程内跑成千上万个协程，IO 等待时自动切换；② asyncio.Semaphore——控制各类资源并发上限（模型 8、SQL 16、工具 32）；③ ThreadPoolExecutor——处理同步阻塞操作（如用 requests 调 Agent Runtime 的 HTTP 流）。

### 31. 什么是 DRR 调度

**答：** DRR（Deficit Round Robin）是一种公平调度算法。每个 `queue_key`（tenant:user）有一个信用值（credit），每轮增加 base_quantum（默认 4）。任务的 estimated_cost 从信用值中扣除。信用不够就跳过，防止大任务饿死小任务。比简单 FIFO 更公平。

### 32. 准入控制是怎么做的

**答：** `create_job` 时检查两个条件：① 全局待处理任务数不超过 `max_pending_jobs`（默认 5000）；② 单用户待处理任务数不超过 `max_pending_per_user`（默认 20）。超限抛 RuntimeError，API 返回 429 Too Many Requests。

### 33. 会话锁的作用是什么

**答：** 防止同一会话同时跑两个任务。调度前通过 `session_locks.acquire(session_id, job_id)` 获取锁，任务完成后释放。如果获取失败，任务回到队列等待。锁有 TTL（默认 120 秒），通过心跳续期。

### 34. 租约机制是什么

**答：** 每个被调度的任务有一个 lease（默认 30 秒 TTL）。Worker 执行期间通过心跳续期。如果 Worker 挂了，租约过期后调度器会检测到（`_recover_stalled_jobs`），把任务重新放回队列。最多重试 `max_attempts`（默认 3）次。

### 35. SSE 事件在后端是怎么分发的

**答：** 发布-订阅模式。每个 Job 有一个 `asyncio.Queue` 集合（subscribers）。后端 `_emit_locked` 产生事件时，写入事件列表并 `put_nowait` 到所有 subscriber 队列。SSE 端点 `subscribe` 创建新队列，`while True` 循环 `queue.get()` 等待事件，yield 为 SSE 帧。

### 36. SSE 帧的格式是什么

**答：** 标准 SSE 格式：`id: {seq}\nevent: {event_type}\ndata: {json_body}\n\n`。seq 是事件序号，支持断线续传（客户端发 `Last-Event-ID` 头）。event_type 包括 queued、admitted、running、telemetry、text_delta、tool_use、tool_result、final、error、cancelled 等。

### 37. keepalive 机制是怎么实现的

**答：** SSE 端点的 `asyncio.wait_for(queue.get(), timeout=15)` 等待 15 秒，超时后发送 `b": keepalive\n\n"`（SSE 注释格式）。这防止 Nginx 等反向代理因长时间无数据而断连。

### 38. 后端怎么调用 Agent Runtime

**答：** 通过 HTTP 代理。Worker 在 `_execute_proxy_blocking` 中用 `requests.post` 调用 Agent Runtime 的 `/api/chat_stream` 接口，传入 prompt 和 session_id，`stream=True` 逐行读取返回的 JSON 事件。每行解析出 event_type，非 final 事件通过 `_emit_from_proxy_thread` 转发给前端。

### 39. 为什么用线程池调 Agent Runtime

**答：** 因为 `requests` 库是同步阻塞的，直接在 asyncio 事件循环里调用会阻塞所有协程。用 `loop.run_in_executor(proxy_executor, func)` 把阻塞操作扔到线程池（8 个线程），事件循环可以继续处理其他请求。

### 40. 幂等性是怎么保证的

**答：** 创建任务时支持 `Idempotency-Key` 头。如果同一个 key 已经创建过任务，直接返回已有任务而不是创建新的。防止前端重试时产生重复任务。

### 41. fencing_token 是什么

**答：** 一个递增的版本号，用于分布式状态一致性。每次任务被调度/重试时 fencing_token +1。读取任务时通过 `_select_freshest_job` 比较 fencing_token，高版本号覆盖低版本号，防止异步持久化导致的状态回退。

### 42. execution_token 的作用是什么

**答：** 每次任务被调度时生成一个 UUID。Worker 执行前检查 execution_token 是否匹配，不匹配说明任务已被重新调度给其他 Worker，当前 Worker 应该放弃。防止重复执行。

### 43. 后端持久化是怎么做的

**答：** 异步写入 MySQL。有一个 `_persistence_queue`（asyncio.Queue，最大 10000），任务状态变更和事件放入队列，`_persistence_loop` 协程消费队列，通过 `ThreadPoolExecutor`（4 个线程）执行实际 SQL 写入。这样不阻塞事件循环。text_delta 事件不持久化，减少写放大。

### 44. Worker 循环是怎么工作的

**答：** `_worker_loop` 从 `_dispatch_queue` 取消息 → 调用 `_execute_job` 执行任务 → 执行完后 `_settle_dispatch_message` 确认或重试 → `dispatch_backend.ack` 确认消息。8 个 Worker 协程并发消费。

### 45. 如果 Worker 执行失败了怎么办

**答：** 异常被捕获，调用 `_retry_dispatch_message`：如果 `attempt_count < max_attempts`（默认 3），任务回到队列头部等待重试；否则标记为 FAILED。释放会话锁，通知前端 error 事件。

### 46. 后端怎么知道 Agent Runtime 返回的结果是否合格

**答：** 检查 `output_contract_status` 字段。如果为 `unmet`，说明 Agent 没有满足输出契约（比如缺少必要的数据），任务标记为 FAILED 而非 SUCCEEDED。同时检查 `task_contract_status` 和 `termination_reason`。

### 47. 任务路由（Task Router）是什么

**答：** `task_router.py` 实现了一个零模型成本的路由器。通过关键词匹配把用户输入分类为不同任务类型：`single_vehicle_attribute_query`（单车属性查询）、`vehicle_attribute_stats`（属性统计）、`cohort_attribute_query`（群体查询）等。高置信度的走确定性工作流（不调模型），低置信度的走 Agent Loop。

### 48. Agent 路由的 RouteCard 机制是什么

**答：** Agent Loop 内部有一个卡片式路由器。每张 RouteCard 定义一个领域（vehicle_spec、manual_qa、trend_analysis、artifact_generation），包含信号词和权重。用户输入对所有卡片评分，选最高分的领域，限制暴露给模型的工具集。比如"车长""轴距"命中 vehicle_spec 路由，优先暴露 SubjectsAttributeLookup 工具。

### 49. 信号量具体控制了什么

**答：** 四类资源各有独立信号量：`_model_sem`（默认 8）控制同时调用模型 API 的数量；`_sql_sem`（默认 16）控制并发 SQL 查询；`_tool_sem`（默认 32）控制并发工具调用；`_artifact_sem`（默认 2）控制并发生成图表/PPT 的数量。

### 50. 后端怎么取消一个正在执行的任务

**答：** `cancel_job` 检查任务状态：如果还在 QUEUED，直接从队列移除并标记 CANCELLED；如果已经 ADMITTED/RUNNING，标记为 CANCEL_REQUESTED，然后通过 HTTP POST 调 Agent Runtime 的 `/api/cancel` 接口，同时关闭正在读取的 SSE 响应流。

### 51. dispatch_backend 是什么

**答：** 任务分发的消息队列后端。支持两种实现：进程内队列（单机模式）和 Redis Stream（分布式模式）。通过 `AGENT_JOB_BROKER` 环境变量切换。Redis 模式下可以多实例部署，Worker 通过 XREADGROUP 竞争消费。

### 52. scheduler_leader 是什么

**答：** 分布式模式下的调度器选主机制。多实例部署时，只有一个实例担任"领导者"负责调度循环。通过 TTL 锁实现，领导者定期续期。如果领导者挂了，TTL 过期后其他实例可以抢占。

### 53. 后端怎么统计任务执行情况

**答：** `stats()` 方法返回：各状态任务数、待处理总数、活跃队列数、dispatch 积压、Worker 并发数、各类信号量上限、持久化积压、调度器状态等。通过 `GET /api/agent/chat_jobs_runtime/status` 暴露给前端。

### 54. 后端 CORS 是怎么配置的

**答：** FastAPI 的 `CORSMiddleware` 配置 `allow_origins`（允许的前端域名）、`allow_methods`（GET/POST 等）、`allow_headers`（Authorization 等）。开发时允许 `localhost:5173`，生产环境通过 Nginx 反向代理避免跨域。

### 55. 如果同时打开 10 个 Tab 发任务，后端怎么处理

**答：** 每个任务创建独立 Job 对象。准入控制检查是否超限（全局 5000、单用户 20）。DRR 调度器按 queue_key 公平轮转选择候选任务。同一会话的任务通过会话锁串行化。8 个 Worker 并发执行，模型信号量（8）控制同时调模型的数量。超出的任务在队列等待。

---

## 三、Agent Runtime：循环、工具、路由（56-80）

### 56. Agent Loop 的核心流程是什么

**答：** ① 路由器判断任务类型，选择工具集；② 构建 system prompt + 上下文 + 历史消息；③ 调用模型 API，模型返回文本或工具调用请求；④ 如果有工具调用，执行工具，把结果追加到对话历史；⑤ 再次调用模型，模型看到工具结果后继续推理；⑥ 循环直到模型返回纯文本（不再调工具）或达到轮次上限。

### 57. Agent 有哪些工具

**答：** 主要工具包括：① `SubjectsAttributeLookup`——车辆属性查询；② `SubjectsAttributeStats`——属性统计聚合；③ `SubjectsSqlQuery/Schema/Glob`——SQL 查询/表结构/搜索；④ `KnowledgeSearch/KnowledgeFetch`——RAG 知识检索；⑤ `WebSearch/WebFetch`——网页搜索和抓取；⑥ `AutoChartGenerate/AutoPptxGenerate`——图表/PPT 生成；⑦ `Read/Edit/Bash/Glob/Grep`——文件操作；⑧ `Agent/TaskStop/SendUserMessage`——控制类工具。

### 58. 工具注册是怎么实现的

**答：** `ToolRegistry` 管理所有工具。每个工具实现 `Tool` 协议：`spec()` 返回 `ToolSpec`（名称、描述、输入 schema、能力标签、执行策略），`run(input, context)` 执行工具逻辑。Registry 提供 `dispatch(call, context)` 分发调用、`list_specs()` 列出所有工具、`list_eligible_specs(context)` 按权限过滤。

### 59. ToolSpec 包含什么信息

**答：** 名称、描述、输入 JSON Schema、别名、是否只读、是否破坏性、最大结果大小、`ToolCapability`（命名空间、动作、实体类型、输入输出模式）、`ToolExecutionPolicy`（风险等级、副作用、超时、重试策略、并发池、是否支持并行/批量）、`ToolDependencies`（依赖服务、必需配置、健康探针）。

### 60. ToolCallScheduler 做了什么

**答：** 工具调用调度器，在分发前做三道防线：① Preflight 检查（权限、依赖健康、输入校验）；② 去重（同一批次内相同调用只执行一次）；③ 批量/并行决策（可并行的工具同时执行，最多 4 个 Worker）。减少无效调用，提高执行效率。

### 61. 工具结果的状态分类有哪些

**答：** `ToolOutcomeStatus` 枚举定义了 13 种状态：SUCCESS、PARTIAL_SUCCESS、NO_DATA、INVALID_INPUT、CAPABILITY_MISMATCH、DATA_COVERAGE_INSUFFICIENT、PERMISSION_DENIED、APPROVAL_REQUIRED、DEPENDENCY_UNHEALTHY、TRANSIENT_FAILURE、PERMANENT_FAILURE、CANCELLED、TIMEOUT。Agent Loop 根据状态决定是否重试、是否禁用该工具。

### 62. RunBudget 是什么

**答：** 运行预算管理器，跟踪一次 Agent 执行的成本和进度：input/output tokens、模型轮次、工具请求/执行/拒绝数、低收益调用数。当 tokens 超过上限或低收益调用超过阈值（默认 3 次），触发终止。防止 Agent 无限循环烧钱。

### 63. Agent 的路由决策是怎么做的

**答：** 两层路由：① L0 TaskRouter（零模型成本）——关键词匹配，高置信度走确定性工作流；② RouteCard 路由器——信号词评分，选择领域（vehicle_spec/manual_qa/trend_analysis/artifact_generation），限制暴露给模型的工具集。两层都不命中则走通用路由（暴露全部工具）。

### 64. 确定性工作流和 Agent Loop 有什么区别

**答：** 确定性工作流（deterministic_workflow）不调用模型，直接根据路由结果调用预定义工具（如 SubjectsAttributeLookup），组装结果返回。零 token 成本，延迟极低。Agent Loop 调用模型进行多轮推理，模型决定调什么工具、怎么组合，灵活但有 token 成本。

### 65. 工具去重是怎么实现的

**答：** `ToolCallScheduler._prepare` 对同一批次内的工具调用做指纹比对。通过 `_stable_fingerprint` 对 (tool_name, input) 做 SHA256 哈希，相同指纹的调用只执行一次，结果共享。避免模型在同一轮重复请求相同数据。

### 66. 工具并行执行的条件是什么

**答：** `ToolCallScheduler._can_parallelize` 检查：① 所有工具标记了 `supports_parallel: true`；② 所有工具是只读的（`is_read_only`）；③ 没有依赖冲突。满足条件时用 `ThreadPoolExecutor`（最多 4 Worker）并行执行。

### 67. 上下文系统（Context System）是什么

**答：** `build_context_prompt` 根据当前对话状态构建系统提示词，包含：Agent 角色定义、可用工具说明、数据边界声明、输出格式要求。`prepare_messages_with_budget` 在上下文窗口接近限制时压缩历史消息，保留最近的关键信息。

### 68. 模型调用失败怎么处理

**答：** 模型 API 调用失败时，Agent Loop 捕获异常，根据错误类型决定：① 可重试（网络超时、429 限流）→ 等待后重试；② 不可重试（参数错误、认证失败）→ 返回 error 事件给前端。RunBudget 记录失败事件。

### 69. Agent 怎么生成引用（Citation）

**答：** 工具返回结果时，`_compact_model_observation` 提取关键信息（查询、匹配数、行数据等）传给模型。模型在最终回答中标注引用 ID。后端从 final 事件中提取 citations 列表，每个引用包含 citation_id、source_type、title、content、url 等。前端去重后展示在右侧面板。

### 70. 工具权限检查是怎么做的

**答：** 每个工具有 `check_permissions(input, context)` 方法。SQL 工具检查数据范围（data_scope）——只允许查询用户权限内的记录。权限不足的返回 `PERMISSION_DENIED` 状态。Agent Loop 收到后不会禁用该工具（模型可以修正查询），但会记录审计事件。

### 71. 工具输出大小怎么控制

**答：** `ToolSpec.max_result_size_chars` 限制工具输出大小（默认 20000 字符）。`_enforce_result_size` 在返回前截断。Agent Loop 层面，`_compact_model_observation` 进一步压缩——只保留前 3 行数据、前 3 个候选项，减少传给模型的 token 数。

### 72. Agent 的对话历史是怎么管理的

**答：** `Conversation` 类管理消息列表。每轮模型调用后，用户消息、助手回复、工具调用和结果都追加到历史。历史过长时 `prepare_messages_with_budget` 压缩早期消息，保留 system prompt + 最近 N 轮对话 + 关键工具结果。

### 73. Provider 抽象层是什么

**答：** `BaseProvider` 定义统一接口：`chat(messages, tools)` 同步调用、`chat_stream(messages, tools)` 流式调用。具体实现包括 `AnthropicProvider`、`MinimaxProvider`、`OpenAICompatibleProvider`、`ArkResponsesProvider` 等。Agent Loop 不关心底层用哪个模型，通过 Provider 抽象层统一调用。

### 74. Agent 的审计事件（Audit Events）有哪些

**答：** 关键审计事件包括：`tool_scheduler_decision`（调度决策：请求数/执行数/拒绝数/并行策略）、`tool_batch_dispatch_completed`（批量执行完成：耗时）、`model_turn`（模型轮次：token 用量）。这些事件记录在 RunBudget 和 ToolContext 中，最终通过 telemetry 推送给前端。

### 75. 工具失败后 Agent 怎么决策

**答：** `_result_disables_tool_for_run` 判断：DEPENDENCY_UNHEALTHY/CAPABILITY_MISMATCH 且不可重试 → 本轮禁用该工具；PERMISSION_DENIED 且是 SQL 数据范围问题 → 不禁用（模型可修正）。Agent Loop 把失败结果传给模型，模型看到错误后可以选择修正输入或换其他工具。

### 76. 什么是 TaskRequirementState

**答：** 任务需求状态跟踪器。Agent 执行过程中，如果发现需要生成特定产物（图表、PPT、JSON），会标记为 "open" 需求。Agent Loop 在每轮结束时检查是否所有需求都已满足（通过 `_completion_tool_names` 匹配对应工具）。未满足的需求影响 `output_contract_status`。

### 77. Agent 的 model_tier 是什么

**答：** 模型分层路由。根据任务复杂度选择不同模型：简单查询用小模型（便宜快速），复杂分析用大模型（准确但贵）。`_model_override_for_tier` 根据 tier 名称映射到具体模型。RunBudget 记录 model_tier 和 budget_class。

### 78. Agent Loop 的终止条件有哪些

**答：** ① 模型返回纯文本（不再请求工具调用）→ 正常完成；② 达到 max_turns（默认 24，最大 100）→ incomplete；③ RunBudget 超限（tokens 过多或低收益调用过多）→ 终止；④ 用户取消 → cancelled；⑤ 模型 API 不可恢复错误 → failed。

### 79. 工具输入校验是怎么做的

**答：** `validate_json_schema` 根据 `ToolSpec.input_schema` 校验模型生成的工具输入。检查必填字段、类型匹配、额外属性。校验失败返回 `INVALID_INPUT` 状态。`input_aliases` 支持字段名别名映射，兼容模型可能使用的不同字段名。

### 80. Agent 的 evidence_status 是什么

**答：** 证据状态标记，表示回答的证据充分程度：`not_applicable`（不需要证据）、`sufficient`（证据充分）、`partial`（部分证据）、`insufficient`（证据不足）。由 Agent 在 final 事件中标记，前端展示在任务洞察面板。

---

## 四、架构设计与工程实践（81-100）

### 81. 这个项目的整体架构是什么

**答：** 三层架构：① 前端（React + TypeScript + Ant Design + Vite）——用户交互层；② 后端（FastAPI + MySQL）——业务逻辑、任务调度、持久化；③ Agent Runtime（Clawd Web Agent）——AI Agent 执行引擎，支持 SQL 工具和 RAG 知识检索双引擎。前后端通过 REST API + SSE 通信，后端通过 HTTP 代理调用 Agent Runtime。

### 82. 为什么选 FastAPI 而不是 Flask 或 Django

**答：** ① 原生 async/await 支持，配合 uvicorn 实现高并发；② 自动 OpenAPI 文档生成；③ 基于 Python 类型提示的请求验证（Pydantic）；④ 性能优于 Flask（异步 IO）。对于需要 SSE 流式推送的 Agent 场景，asyncio 支持是关键。

### 83. 为什么选 React 而不是 Vue 或 Angular

**答：** ① 生态最大，组件库和工具链丰富；② 状态驱动的 UI 模型适合实时数据展示（SSE 事件流）；③ TypeScript 支持好；④ AI 辅助开发时 React 代码生成质量高。

### 84. 为什么前后端分离

**答：** ① 独立部署——前端放 CDN，后端独立扩容；② 职责隔离——前端管展示，后端管逻辑；③ 技术栈独立——前端可以换框架不影响后端；④ 团队协作——前端和后端可以并行开发。

### 85. 这个项目的数据流是怎样的

**答：** 用户输入 → React 状态更新 → POST 创建 Job → FastAPI 验证+调度 → Worker 调 Agent Runtime → Agent Loop 多轮推理（调模型+调工具）→ 结果通过 SSE 逐帧推送 → 前端解析事件 → React 状态更新 → UI 自动刷新。

### 86. 这个项目的容错机制有哪些

**答：** ① 任务重试——Worker 失败后最多重试 3 次；② 租约恢复——Worker 挂了，租约过期后任务自动重新排队；③ 幂等创建——Idempotency-Key 防止重复创建；④ 准入控制——防止系统过载；⑤ RunBudget——防止 Agent 无限循环；⑥ 工具 Preflight——执行前检查依赖健康；⑦ 信号量——防止资源耗尽。

### 87. 这个项目的安全机制有哪些

**答：** ① Bearer Token 鉴权——每个请求验证身份；② 数据范围（data_scope）——限制用户只能查询权限内的数据；③ 工具权限检查——SQL 工具检查 active record 范围；④ 会话锁——防止同一会话并发冲突；⑤ execution_token——防止重复执行；⑥ fencing_token——防止分布式状态回退；⑦ 工具风险等级——`ToolExecutionPolicy.risk` 标记破坏性操作。

### 88. 这个项目的可观测性是怎么做的

**答：** ① 审计事件——工具调度决策、批量执行、模型轮次都有结构化日志；② RunBudget——token 用量、工具调用统计、低收益调用计数；③ ToolSchedulerLedger——请求/执行/拒绝数、状态分布、拒绝原因分布；④ FlowEvents——完整的事件链路（源→目标→标签→耗时）；⑤ Runtime Status API——暴露系统运行状态。

### 89. 如果 Agent 执行超时了怎么办

**答：** 多层保护：① 工具级超时——`ToolExecutionPolicy.timeout_s`；② Agent Runtime 级——`AGENT_JOB_UPSTREAM_READ_TIMEOUT_SECONDS`；③ RunBudget 级——tokens 超限或低收益调用过多自动终止；④ max_turns 限制——最多 100 轮；⑤ 租约机制——Worker 心跳超时后任务被回收。

### 90. 这个项目的数据库 schema 是怎么设计的

**答：** 核心表 `agent_chat_jobs` 存储任务状态：job_id、tenant_id、user_id、session_id、status、prompt、max_turns、fencing_token、execution_token、lease_expires_at、heartbeat_at、attempt_count、idempotency_key、final_text、final_metadata 等。支持异步写入，通过幂等键去重。

### 91. 怎么做本地开发环境搭建

**答：** `scripts/dev_platform.sh` 一键启动三个服务：前端 Vite（5173）、后端 FastAPI（8000）、Agent Runtime（7862）。环境变量在 `.env.local` 中配置（API Key、数据库连接等）。

### 92. 怎么做生产部署

**答：** ① 前端打包为静态文件，部署到 Nginx/CDN；② 后端 FastAPI 用 uvicorn + gunicorn 部署，可多实例 + 负载均衡；③ Agent Runtime 独立部署；④ MySQL 主从复制；⑤ 可选 Redis 作为 dispatch backend 支持分布式调度；⑥ Nginx 反向代理统一域名，解决跨域。

### 93. 这个项目的测试策略是什么

**答：** ① pytest 做后端单元测试；② `tests/test_agent_production.py` 测试 Agent 生产场景；③ `tests/test_core.py` 测试核心逻辑；④ `tests/test_knowledge.py` 测试 RAG 知识检索；⑤ `tests/test_pdf_chunking.py` 测试 PDF 分块；⑥ `evals/` 目录做 Agent 效果评估。

### 94. 如果让你扩展一个新工具，怎么做

**答：** ① 创建新类实现 `Tool` 协议：`spec()` 返回 ToolSpec（名称、描述、输入 schema、能力标签）；② `run(input, context)` 实现执行逻辑；③ 在 `ToolRegistry` 注册；④ 定义 `ToolCapability` 让路由器能识别；⑤ 设置 `ToolExecutionPolicy`（风险等级、超时、是否支持并行）。

### 95. 这个项目的 RAG 是怎么实现的

**答：** 通过 RAGFlow 做文档分块和向量检索。`KnowledgeSearch` 工具调用 RAGFlow API 搜索相关文档片段；`KnowledgeFetch` 获取完整文档内容。搜索结果作为引用（citation）传给模型，模型在回答中引用。支持 dataset_id 配置和 API Key 鉴权。

### 96. 这个项目的 SQL 工具是怎么工作的

**答：** `SubjectsSqlQuery` 工具接收 SQL 语句，在受控数据库中执行，返回查询结果。有权限检查——只允许查询用户 data_scope 内的记录。`SubjectsSqlSchema` 返回表结构，`SubjectsSqlGlob` 搜索表/列名。Agent Loop 中模型根据 Schema 生成 SQL，工具执行后返回结果。

### 97. 这个项目的视觉模型是什么

**答：** 基于 MMDetection/YOLO 的目标检测模型，用于汽车底盘零部件识别。通过 Docker Compose 部署推理服务。`VisionPage` 前端页面提供图片上传和检测结果展示。与 Agent 系统相对独立，属于平台的数据采集能力。

### 98. 如果并发量增大 10 倍，系统瓶颈在哪

**答：** ① 模型 API 并发上限（默认 8）——需要提升 API 配额或加模型缓存；② MySQL 写入压力——持久化队列可能积压，需要优化批量写入或换用消息队列；③ 单机内存——所有 Job 状态在内存中，需要分布式存储；④ Agent Runtime 单点——需要多实例 + Redis dispatch backend。

### 99. 这个项目的亮点是什么

**答：** ① 生产级 Agent 调度系统——DRR 公平调度、租约恢复、幂等创建、准入控制；② 双引擎数据能力——SQL 结构化查询 + RAG 知识检索；③ 零模型成本路由——L0 TaskRouter + RouteCard 信号词路由；④ 透明可追溯——完整的事件链路、引用溯源、运行预算；⑤ 工具调度器——Preflight 检查、去重、并行执行。

### 100. 你对这个项目的未来规划是什么

**答：** ① 分布式调度升级——全面迁移到 Redis Stream，支持多实例水平扩展；② 模型路由优化——根据任务复杂度自动选择模型 tier，降低成本；③ 前端增强——增加任务对比、历史回放、自定义工作流；④ 可观测性增强——OpenTelemetry 集成、全链路追踪；⑤ 工具生态扩展——MCP 协议支持更多外部工具。
