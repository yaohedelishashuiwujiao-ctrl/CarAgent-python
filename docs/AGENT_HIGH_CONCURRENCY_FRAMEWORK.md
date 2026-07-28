# Agent 高并发框架设计规格

## 1. 目标

本文件是后续开发 Agent 高并发能力的固定设计依据。除非明确更新本文件，否则实现应按这里的模块边界、状态机、接口和调度算法推进。

目标：

- 支持至少 2000 个用户同时在线、发起问题、等待结果或接收流式输出。
- 避免 2000 个请求直接同时执行完整 agent loop，防止模型、SQL、工具执行和会话状态被打爆。
- 支持公司内网约束：默认不开放 WebSearch；WebFetch 只能访问允许域名，例如汽车之家。
- 保留现有工具调用观测能力：tool use、tool result、tokens、引用、错误、耗时都要可追踪。
- 支持水平扩展：可以增加 worker 实例提升实际吞吐。

非目标：

- 不承诺 2000 个完整模型推理同时运行。真实并发执行量由模型供应商 QPS、平均延迟、数据库连接池和 worker 数共同决定。
- 不把并发能力建立在单机线程无限增长上。
- 不把调度策略写死到某个车型、字段或单一业务问题。

## 2. 核心原则

1. 接入层负责连接，不负责执行长任务。
2. 提问先变成 job，再由调度器决定何时执行。
3. 同一 session 串行执行，避免会话历史并发写入。
4. 不同用户公平调度，不能让单个用户或脚本占满所有 worker。
5. 模型、SQL、工具、文件生成分别限流。
6. 所有关键状态必须可恢复，进程重启不能丢失最终结果。
7. 流式输出来自事件表或事件流，前端断线后可以重连。

## 3. 总体架构

```text
Frontend
  |
  | 1. POST /api/agent/chat_jobs
  v
Backend API / Access Plane
  |
  | 2. 写入 chat_jobs，返回 job_id
  v
Job Store + Event Store
  |
  | 3. Scheduler 按公平队列取 job
  v
Agent Worker Pool
  |
  | 4. 调用 LLM、SQL、Knowledge、AutoHome WebFetch、生成工具
  v
Job Event Stream
  |
  | 5. GET /api/agent/chat_jobs/{job_id}/events
  v
Frontend
```

推荐进程拆分：

- `backend-api`：FastAPI，对外提供 HTTP、SSE、鉴权、准入。
- `agent-scheduler`：扫描待执行任务，执行公平调度和资源令牌分配。
- `agent-worker`：执行 agent loop，写入事件和最终结果。
- `redis`：队列、轻量锁、事件流、限流计数。初期可用 SQL 代替队列，但最终建议 Redis。
- `mysql/postgres`：持久化 job、session、event、trace、final answer。

## 4. 请求生命周期

1. 用户提交问题。
2. Backend 校验权限、session、prompt 长度和 quota。
3. Backend 创建 `chat_job`，状态为 `queued`。
4. Backend 返回 `202 Accepted`，包含 `job_id`、`status_url`、`events_url`。
5. 前端连接 `events_url` 接收 SSE。
6. Scheduler 按公平队列选择 job。
7. Worker 把 job 状态改为 `running`，开始执行 agent loop。
8. Worker 每次模型请求、工具调用、工具结果、文本增量都写入事件。
9. Worker 成功后写最终答案，状态改为 `succeeded`。
10. 前端收到 `final` 事件后更新会话。

失败流程：

- 任务排队超限：`429 Too Many Requests`。
- 同一 session 已有运行任务：新任务保持 `queued` 或按策略返回 `409`。生产推荐排队，开发期可返回 `409`。
- 用户取消：状态改为 `cancel_requested`，worker 在下一次安全检查点停止，最终变成 `cancelled`。
- worker 崩溃：scheduler 根据 heartbeat 将 `running` 超时任务改回 `queued` 或 `failed_retryable`。

## 5. 状态机

```text
queued
  -> admitted
  -> running
  -> succeeded
  -> failed
  -> cancelled

queued
  -> rejected

running
  -> cancel_requested
  -> cancelled

running
  -> stalled
  -> queued
```

状态定义：

- `queued`：任务已入队，未分配 worker。
- `admitted`：scheduler 已选中，正在等待资源令牌。
- `running`：worker 正在执行。
- `succeeded`：任务完成，已有最终回答。
- `failed`：不可自动恢复的失败。
- `failed_retryable`：可重试失败，例如 worker 异常退出。
- `cancel_requested`：用户请求取消。
- `cancelled`：已取消。
- `rejected`：准入失败。
- `stalled`：运行超时或 heartbeat 过期。

## 6. 数据模型

### 6.1 `agent_chat_jobs`

```sql
CREATE TABLE agent_chat_jobs (
  id VARCHAR(64) PRIMARY KEY,
  tenant_id VARCHAR(64) NOT NULL DEFAULT 'default',
  user_id VARCHAR(64) NOT NULL,
  session_id VARCHAR(128) NOT NULL,
  idempotency_key VARCHAR(128) NULL,
  prompt_hash VARCHAR(64) NOT NULL,
  prompt_text TEXT NOT NULL,
  status VARCHAR(32) NOT NULL,
  priority INT NOT NULL DEFAULT 0,
  queue_key VARCHAR(256) NOT NULL,
  estimated_cost INT NOT NULL DEFAULT 1,
  max_turns INT NOT NULL DEFAULT 20,
  model_provider VARCHAR(64) NULL,
  model_name VARCHAR(128) NULL,
  assigned_worker_id VARCHAR(128) NULL,
  attempt_count INT NOT NULL DEFAULT 0,
  queued_at DATETIME NOT NULL,
  started_at DATETIME NULL,
  finished_at DATETIME NULL,
  heartbeat_at DATETIME NULL,
  error_code VARCHAR(64) NULL,
  error_message TEXT NULL,
  final_text MEDIUMTEXT NULL,
  input_tokens INT NOT NULL DEFAULT 0,
  output_tokens INT NOT NULL DEFAULT 0,
  tool_call_count INT NOT NULL DEFAULT 0,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  UNIQUE KEY uniq_agent_job_idempotency (tenant_id, user_id, idempotency_key),
  KEY idx_agent_jobs_status_priority (status, priority, queued_at),
  KEY idx_agent_jobs_session_status (session_id, status),
  KEY idx_agent_jobs_queue_key (queue_key, queued_at)
);
```

### 6.2 `agent_chat_events`

```sql
CREATE TABLE agent_chat_events (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  job_id VARCHAR(64) NOT NULL,
  seq BIGINT NOT NULL,
  event_type VARCHAR(64) NOT NULL,
  payload JSON NOT NULL,
  created_at DATETIME NOT NULL,
  UNIQUE KEY uniq_agent_event_seq (job_id, seq),
  KEY idx_agent_events_job_id (job_id, id)
);
```

事件类型：

- `queued`
- `admitted`
- `running`
- `model_request`
- `model_response`
- `tool_use`
- `tool_result`
- `text_delta`
- `telemetry`
- `final`
- `error`
- `cancelled`

### 6.3 `agent_session_locks`

SQL 版本：

```sql
CREATE TABLE agent_session_locks (
  session_id VARCHAR(128) PRIMARY KEY,
  owner_id VARCHAR(128) NOT NULL,
  expires_at DATETIME NOT NULL,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL
);
```

Redis 版本：

```text
SET agent:session_lock:{session_id} {worker_id} NX PX {ttl_ms}
```

worker 运行期间续租。释放时必须校验 owner。

## 7. Redis Key 设计

生产推荐 Redis 做调度热路径：

```text
agent:queue:keys                       ZSET，活跃 queue_key 列表
agent:queue:{queue_key}                LIST，某个用户/租户队列
agent:job:{job_id}                     HASH，job 热状态
agent:events:{job_id}                  STREAM，job 事件流
agent:session_lock:{session_id}        STRING，分布式 session 锁
agent:rate:user:{user_id}              STRING/COUNTER，用户限流
agent:rate:tenant:{tenant_id}          STRING/COUNTER，租户限流
agent:worker:{worker_id}               HASH，worker heartbeat
agent:resource:model:{provider}        TOKEN BUCKET，模型资源令牌
agent:resource:sql                     TOKEN BUCKET，SQL 资源令牌
agent:resource:tool                    TOKEN BUCKET，工具执行令牌
```

SQL 仍是最终事实来源。Redis 事件可以设置 TTL，但 final answer 和关键 trace 必须落 SQL。

## 8. API 契约

### 8.1 创建任务

`POST /api/agent/chat_jobs`

请求：

```json
{
  "session_id": "uuid",
  "prompt": "小米SU7轴距",
  "max_turns": 20,
  "idempotency_key": "client-generated-key"
}
```

响应：

```json
{
  "job_id": "job_01...",
  "status": "queued",
  "queue_position": 12,
  "status_url": "/api/agent/chat_jobs/job_01...",
  "events_url": "/api/agent/chat_jobs/job_01.../events"
}
```

### 8.2 查询任务

`GET /api/agent/chat_jobs/{job_id}`

响应：

```json
{
  "job_id": "job_01...",
  "status": "running",
  "session_id": "uuid",
  "queued_at": "2026-07-12T22:00:00+08:00",
  "started_at": "2026-07-12T22:00:03+08:00",
  "final_text": null,
  "usage": {
    "input_tokens": 1200,
    "output_tokens": 300,
    "tool_call_count": 1
  }
}
```

### 8.3 事件流

`GET /api/agent/chat_jobs/{job_id}/events?after_seq=0`

SSE 格式：

```text
id: 14
event: tool_result
data: {"seq":14,"tool":"SubjectsAttributeLookup","status":"ok","rows":4}
```

前端断线重连时用 `Last-Event-ID` 或 `after_seq` 补齐事件。

### 8.4 取消任务

`POST /api/agent/chat_jobs/{job_id}/cancel`

响应：

```json
{
  "job_id": "job_01...",
  "status": "cancel_requested"
}
```

## 9. 调度算法

### 9.1 Queue Key

默认：

```text
queue_key = tenant_id + ":" + user_id
```

同一 session 通过 session lock 串行，不放进 queue key，否则同一用户多个 session 会互相阻塞。

### 9.2 Deficit Round Robin

每个 `queue_key` 有一个 FIFO 队列和一个 credit。

参数：

- `BASE_QUANTUM=4`
- `MAX_CREDIT=32`
- `DEFAULT_JOB_COST=1`
- 简单 lookup cost = 1
- 普通 agent cost = 4
- 报告/PPT/图表 cost = 8

调度循环：

```text
for key in active_keys_round_robin:
  credit[key] = min(MAX_CREDIT, credit[key] + weight[key] * BASE_QUANTUM)
  job = peek(queue[key])
  if job is None:
    remove key
    continue
  if credit[key] < job.estimated_cost:
    continue
  if not session_lock_available(job.session_id):
    continue
  if not resource_governor.can_acquire(job.resource_plan):
    continue
  pop(queue[key])
  credit[key] -= job.estimated_cost
  dispatch_to_worker(job)
```

好处：

- 一个用户大量提交不会饿死其他用户。
- 高成本任务会自然少跑，低成本任务吞吐更高。
- 可以用 tenant weight 做企业客户优先级。

## 10. Resource Governor

执行前必须拿到资源令牌：

```text
resource_plan = {
  model: 1,
  sql: 1,
  tool: 1,
  artifact: 0
}
```

令牌类型：

- `model:{provider}`：模型并发。按供应商单独限流。
- `sql`：数据库并发。不得超过连接池可承受上限。
- `tool`：普通 Python 工具并发。
- `artifact`：PPT、图表、文件生成并发。

模型并发估算：

```text
model_concurrency = floor(provider_qps * avg_model_latency_seconds / avg_model_calls_per_job)
```

示例：

```text
provider_qps = 2
avg_model_latency_seconds = 8
avg_model_calls_per_job = 2
model_concurrency = floor(2 * 8 / 2) = 8
```

这表示：即使 2000 人在线，当前 provider quota 下也只应同时跑约 8 个模型型任务，其余任务排队。

## 11. Worker 执行模型

worker 主循环：

```text
while running:
  job = reserve_job()
  if not job:
    sleep(short_interval)
    continue
  try:
    heartbeat(job)
    acquire_session_lock(job.session_id)
    load_session(job.session_id)
    run_agent_loop_with_event_sink(job)
    persist_final_answer(job)
    mark_succeeded(job)
  except Cancelled:
    mark_cancelled(job)
  except RetryableError:
    release_resources()
    requeue_or_mark_retryable(job)
  except Exception:
    mark_failed(job)
  finally:
    release_session_lock(job.session_id)
    release_resource_tokens(job)
```

取消检查点：

- 每次模型请求前。
- 每次工具调用前。
- 每个工具结果写入后。
- 每轮 agent loop 开始前。

## 12. Agent Loop 集成要求

现有 `run_agent_loop` 不应直接感知 HTTP 请求。需要增加可插拔接口：

```python
class AgentEventSink:
    def emit(self, event_type: str, payload: dict) -> None: ...
    def is_cancelled(self) -> bool: ...
```

worker 传入 event sink：

- 模型请求前 emit `model_request`。
- 工具调用前 emit `tool_use`。
- 工具返回后 emit `tool_result`。
- 文本流式输出 emit `text_delta`。
- 结束 emit `final`。

`run_agent_loop` 内部仍保持工具路由、引用修复和低收益停止逻辑，不把调度逻辑塞进 agent loop。

## 13. 数据一致性

必须保证：

- 同一 `session_id` 同一时间只有一个 running job。
- job final answer 写入成功后，session message 才能标记完成。
- event seq 对同一 job 单调递增。
- worker 只能更新自己持有锁的 job。
- 幂等 key 重复提交时返回已有 job，不创建第二个 job。

会话写入顺序：

```text
create job
append user message as pending
run worker
append assistant final message
mark user message committed
mark job succeeded
```

## 14. 配置项

```text
AGENT_JOB_BROKER=redis
AGENT_MAX_PENDING_JOBS=5000
AGENT_MAX_PENDING_PER_USER=20
AGENT_MAX_RUNNING_PER_USER=2
AGENT_MAX_RUNNING_PER_SESSION=1
AGENT_SCHEDULER_TICK_MS=100
AGENT_WORKER_CONCURRENCY=64
AGENT_MODEL_CONCURRENCY_ARK=64
AGENT_SQL_CONCURRENCY=16
AGENT_TOOL_CONCURRENCY=32
AGENT_ARTIFACT_CONCURRENCY=2
AGENT_JOB_HEARTBEAT_SECONDS=5
AGENT_JOB_STALL_SECONDS=60
AGENT_JOB_MAX_ATTEMPTS=3
AGENT_EVENT_RETENTION_DAYS=7
CLAWD_MAX_CONCURRENT_AGENT_RUNS=64
CLAWD_MAX_TOOL_CALLS_PER_RUN=6
CLAWD_ENABLE_WEBSEARCH=0
CLAWD_WEBFETCH_ALLOWED_DOMAINS=autohome.com.cn
```

`64` 是当前单机起始值，不是固定容量。生产环境按模型端实际延迟、429 比例和机器资源做阶梯压测，逐步调整；接入容量与模型执行并发必须分开计算。

## 15. 观测指标

必须采集：

- `agent_jobs_queued`
- `agent_jobs_running`
- `agent_jobs_succeeded_total`
- `agent_jobs_failed_total`
- `agent_queue_wait_ms_p50/p95/p99`
- `agent_job_duration_ms_p50/p95/p99`
- `agent_model_latency_ms_p50/p95/p99`
- `agent_tool_calls_per_job`
- `agent_invalid_tool_calls_total`
- `agent_sql_latency_ms_p95`
- `agent_provider_429_total`
- `agent_session_lock_conflicts_total`
- `agent_events_stream_reconnect_total`

日志必须包含：

- `job_id`
- `session_id`
- `user_id`
- `queue_key`
- `worker_id`
- `tool_name`
- `model_provider`
- `status`
- `elapsed_ms`

## 16. 2000 并发验收标准

验收要分两类：

### 16.1 连接并发

- 2000 个客户端同时连接事件流。
- 后端进程无崩溃。
- 内存增长可控。
- 空闲连接不占用 agent worker。

### 16.2 提问并发

压测输入：

- 2000 个用户在 60 秒内提交问题。
- 80% 简单参数问题。
- 15% 对比/分析问题。
- 5% 报告/图表类重任务。

期望：

- 接口成功创建 job 或明确返回 429。
- `queued + running + finished + rejected = submitted`。
- 没有 session 历史交叉污染。
- provider 429 不持续放大，调度器能降并发。
- p95 队列等待时间有指标可解释。

### 16.3 2026-07-14 真实链路基线

通过前端 Agent API、Redis Stream、MySQL job/event 持久化和真实模型执行完成首轮概率到达压测：

- 在线用户：2000。
- 观察窗口：60 秒。
- 每用户平均提问间隔：600 秒，提问概率约 9.52%。
- 实际问题：187，涉及 183 个用户，全部得到 `202 Accepted`。
- 队列等待：p50 0.165 秒，p95 0.657 秒。
- 首轮配置：128 个模型/worker 并发，客户端硬超时 240 秒。
- 240 秒内完成 57 个，118 个客户端超时；已证明 128 路真实多步 Agent 会让模型延迟显著放大，不能把供应商 QPS 直接等同于可并行 Agent 数。
- 已完成的请求平均 5.175 轮、5.667 次工具调用；工具路径仍偏长。

基线发现并修复：Redis 消息字段解码、每 worker 一个阻塞 Redis 连接导致的连接耗尽、旧 pending 消息抢占新任务、dispatch 重试死循环、heartbeat 小于 lease、取消任务重启后错误恢复。下一轮按 64 个执行并发和 6 次可配置工具预算复测。验收以全量报告 `/tmp/agent_frontend_load_2000_real.json` 为准。

## 17. 开发里程碑

### M1：框架落地

- 新增 `agent_jobs` 和 `agent_events` 数据表。
- 新增 create/status/events/cancel API。
- 保留旧 `/chat_stream`，但前端可以切到 job 模式。
- 实现 SQL-backed queue，先不依赖 Redis。

### M2：worker 化

- 从 HTTP 请求线程中移除 agent loop 执行。
- 新增 `agent_worker` 进程，通过 `AGENT_JOB_ROLE=worker` 独立消费调度队列。
- 实现 session lock、heartbeat、stalled job 恢复。
- 实现 event sink。

### M3：公平调度

- 实现 queue_key FIFO。
- 实现 Deficit Round Robin。
- 实现 per-user/per-session/global quota。
- 实现资源令牌。

### M4：Redis 热路径

- Redis queue 和 Redis stream，API 侧入队、worker 侧消费。
- 分布式 session lock，worker 侧通过 lease 续租，pending 消息通过 consumer group reclaim 恢复。
- 多 worker 实例水平扩展。
- SSE 从 Redis stream 或 SQL event 表恢复。

### M5：压测和自适应限流

- 2000 连接压测。
- 2000 提交压测。
- provider 429 自适应降并发。
- Prometheus/Grafana 仪表板。

## 18. 和当前代码的衔接

当前状态：

- `agent_runtime/web_app.py` 已有临时 in-process 并发保护。
- `agent_runtime/src/tool_system/agent_loop.py` 已有直接属性查询的首轮工具收窄。
- `backend/app/services/agent_jobs.py` 已拆出 job API、调度器和 worker role；Redis Stream broker 已接入为可选热路径。
- `backend/app/services/agent_job_locks.py` 已接入分布式 session lock 和 heartbeat lease。
- `backend/app/services/agent_job_persistence.py` 已支持 execution token、heartbeat 和 lease 到期恢复字段。
- `backend/app/routers/agent_runtime.py` 仍是阻塞代理，需要在 M1/M2 中替换为 job API。

后续开发规则：

- 新代码优先放在 `backend/app/services/agent_jobs.py`、`backend/app/routers/agent_jobs.py`、`agent_runtime/src/worker/`。
- 旧 `/api/agent/chat_stream` 保留兼容，但不再作为高并发主路径。
- 调度逻辑不能写进前端，也不能塞进模型 prompt。
- 工具调用优化放在 tool routing / tool policy 层，不能按固定车型或固定字段写死。
