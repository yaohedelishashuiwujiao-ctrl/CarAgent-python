# 常用高并发框架调研

## 1. 调研结论

适合当前 Agent 系统的高并发框架不是单一技术，而是一组成熟模式组合：

```text
异步接入层 + 持久化任务队列 + Worker Pool + 事件流 + 背压/限流 + 分布式锁 + 可观测性
```

对当前项目最合适的落地路线：

```text
FastAPI/ASGI 接入
  + SQL-backed job store 起步
  + Redis Streams / Redis Queue 做热路径
  + 自研轻量 Scheduler 做公平调度
  + Agent Worker 进程池执行 run_agent_loop
```

不建议直接把 Celery 当成完整调度核心，因为我们的任务需要：

- 同一 session 串行。
- 模型 QPS/token 维度限流。
- 工具调用分资源限流。
- SSE 可恢复事件流。
- 简单问题和重任务按 cost 公平调度。

Celery 很适合通用异步任务，但公平队列、模型资源令牌、session lock、流式事件这几块仍需要我们自己补。

## 2. 常见框架/模式对比

| 类型 | 代表框架 | 适合场景 | 优点 | 局限 | 对本项目建议 |
|---|---|---|---|---|---|
| 异步 HTTP 接入 | FastAPI / Starlette / Uvicorn | 高并发连接、SSE、WebSocket、API 网关 | Python 生态，和现有后端一致，async 支持好 | 不能直接承担长时间 agent loop | 保留 FastAPI，改为 job API + SSE |
| 通用任务队列 | Celery + Redis/RabbitMQ | 后台任务、重试、worker 横向扩展 | 成熟，worker 管理完善 | 自定义公平调度和流式事件较重 | 可参考，不作为第一版核心 |
| 轻量 Python 队列 | Dramatiq / RQ / arq | 简单后台任务 | 比 Celery 简洁 | 生态和复杂调度能力弱 | 不优先 |
| Redis Streams | Redis Stream + Consumer Group | 任务事件、可恢复消费、轻量消息流 | 部署简单，延迟低，适合内网 | 超大规模日志保留不如 Kafka | 推荐 M4 引入 |
| Kafka | Kafka Consumer Group | 大规模事件流、日志、跨系统集成 | 高吞吐、高可靠、分区消费成熟 | 运维重，开发复杂 | 当前不必上，除非事件规模扩大 |
| Reactive 框架 | Spring WebFlux / Reactor | Java 非阻塞服务、背压流 | 背压模型成熟 | 技术栈切换成本大 | 作为思想参考，不迁移 |
| 网络框架 | Netty / Vert.x | 高性能网络服务 | 事件循环成熟，吞吐高 | Java/JVM 栈，离当前项目远 | 不迁移，只参考事件循环模型 |
| Actor 模型 | Akka / Erlang/OTP | 分布式状态机、隔离、监督树 | 容错模型强，适合复杂分布式系统 | 引入成本高 | session/worker 设计可借鉴 actor 思想 |
| Go 并发 | goroutine + channel | 高并发服务、worker pool | 简洁，资源占用低 | 重写成本高 | 不迁移，参考 channel/worker pool |

## 3. 关键模式

### 3.1 Reactor / Async I/O

核心思想：

- 一个或少量事件循环处理大量连接。
- I/O 等待期间不占用业务线程。
- 适合 SSE/WebSocket、HTTP 长连接、代理层。

对本项目的意义：

- 2000 在线用户应该由 async 接入层承载。
- 不要让 `/chat_stream` 一个请求占一个线程直到 agent 完成。
- HTTP 层只负责创建 job、推事件，不直接跑模型。

参考：FastAPI 官方文档说明 async/await 适用于等待网络、文件、数据库、远程 API 等 I/O 场景；这些等待时间可以让程序切去处理其他工作。

### 3.2 Task Queue / Worker Pool

核心思想：

- 请求转成任务。
- worker 从队列拉任务执行。
- 可以多 worker、多机器扩展。

Celery 官方文档把 task queue 定义为跨线程或机器分发工作的机制；client 把消息放进队列，broker 再交给 worker。Celery 也支持 Redis/RabbitMQ broker、多个 worker 和水平扩展。

对本项目的意义：

- 2000 个提问先进入 `agent_chat_jobs`。
- worker 数量和并发度独立扩展。
- 模型 QPS 不够时队列吸收突发流量。

### 3.3 Consumer Group / 分区消费

核心思想：

- 多个 consumer 组成 group。
- 分区在 consumer 之间分配。
- consumer 崩溃后，分区会重新分配。

Kafka/Confluent 文档中，consumer group 会协作消费 topic；分区按成员分配，成员变化时 rebalance。这个思想适合 worker 横向扩展。

对本项目的意义：

- 后续 Redis Streams 或 Kafka 都可以用 consumer group 思路。
- worker 崩溃后，任务不能丢，要能被其他 worker 接手。
- 需要 heartbeat、lease、stalled job recovery。

### 3.4 Backpressure / 背压

核心思想：

- 下游处理不过来时，上游不能无限生产。
- 队列长度、令牌桶、并发 semaphore 都是背压手段。

对本项目的意义：

- 模型 QPS=100 也不能无限开 active agent。
- SQL、工具、PPT 生成都要单独限流。
- 超过队列容量时必须明确返回 429，而不是拖死服务。

### 3.5 Bulkhead / 舱壁隔离

核心思想：

- 不同资源池隔离。
- 一个慢资源不能拖垮整个系统。

对本项目的意义：

- 简单参数查询、复杂分析、报告生成分资源池。
- PPT/图表生成不能占满普通问答 worker。
- WebFetch 慢不能拖死 SQL 查询。

### 3.6 Circuit Breaker / 熔断

核心思想：

- 下游持续失败或超时时，短时间拒绝调用。
- 防止重试风暴。

对本项目的意义：

- 模型 provider 429/5xx 增多时降低并发。
- SQL 慢查询或连接池耗尽时暂停新 SQL-heavy job。
- WebFetch 失败率高时转回已有证据或内网知识库。

### 3.7 Actor / Session 串行化

核心思想：

- 每个 actor 独占自己的状态。
- 外部只能通过消息驱动 actor。

对本项目的意义：

- 每个 `session_id` 可以视作一个逻辑 actor。
- 同一 session 的 job 串行执行。
- 避免两个 agent loop 同时修改同一段会话历史。

## 4. 框架选择建议

### 4.1 第一版：SQL-backed Queue

适合 M1/M2。

```text
FastAPI
  -> agent_chat_jobs table
  -> scheduler scans queued jobs
  -> worker process executes jobs
  -> agent_chat_events table
  -> SSE reads event table
```

优点：

- 改动小。
- 不强依赖新中间件。
- 状态容易排查。
- 适合把框架跑通。

缺点：

- 高频事件写入压力较大。
- queue scan 需要 careful index。
- 多 scheduler 需要行锁或 lease。

### 4.2 第二版：Redis Streams 热路径

适合 M3/M4。

```text
FastAPI
  -> SQL job store
  -> Redis queue / stream
  -> scheduler + worker consumer group
  -> Redis stream events
  -> async SSE
  -> SQL final persistence
```

优点：

- 低延迟。
- 适合事件流。
- consumer group 模型自然支持 worker 扩展。
- Redis 也可做 session lock、rate limit。

缺点：

- 需要处理 Redis 和 SQL 的一致性。
- 事件保留策略要设计。

### 4.3 不建议第一版直接 Kafka

Kafka 很强，但当前项目没有跨系统海量事件流需求。先上 Kafka 会增加运维和开发复杂度。除非后续事件规模达到：

- 多业务系统订阅 agent event。
- 每天千万级以上事件。
- 需要长期 event replay。
- 已有 Kafka 基础设施。

否则 Redis Streams 更合适。

### 4.4 不建议迁移到 Java Reactive 栈

Spring WebFlux、Netty、Vert.x 都很成熟，但当前项目是 Python/FastAPI/agent_runtime。迁移语言栈会带来额外复杂度。我们可以借鉴：

- 事件循环。
- 非阻塞 I/O。
- 背压。
- worker 隔离。

但不需要重写。

## 5. 对当前 Agent 高并发规格的修正建议

当前 [AGENT_HIGH_CONCURRENCY_FRAMEWORK.md](./AGENT_HIGH_CONCURRENCY_FRAMEWORK.md) 的方向是合理的。根据调研，建议确认以下技术选型：

### 5.1 主路径

```text
FastAPI async API
  + SQL job/event table
  + custom scheduler
  + agent worker process pool
  + Redis Streams in phase 2
```

### 5.2 调度策略

保留现有设计：

```text
Deficit Round Robin
  + session lock
  + model/sql/tool/artifact resource tokens
```

理由：

- Celery/Kafka 的默认队列无法直接满足模型资源令牌和 session 串行。
- DRR 比普通 FIFO 更适合多用户公平。
- cost-based 调度能避免 PPT/报告任务压住简单问题。

### 5.3 Worker 模型

推荐：

```text
worker process
  -> internal async tasks for model/tool I/O
  -> bounded semaphores
  -> heartbeat
  -> event sink
```

不推荐：

```text
ThreadingHTTPServer request thread
  -> directly run full agent loop
```

### 5.4 事件流

第一版：

```text
SSE reads SQL agent_chat_events
```

第二版：

```text
SSE reads Redis Streams
SQL stores final answer and compact trace
```

## 6. 推荐最终架构

```text
Frontend
  |
FastAPI Access Layer
  |-- create job
  |-- stream events
  |-- cancel job
  |
SQL Job Store
  |
Scheduler
  |-- Deficit Round Robin
  |-- resource tokens
  |-- session lock
  |
Redis
  |-- queues
  |-- streams
  |-- locks
  |-- rate limits
  |
Agent Workers
  |-- run_agent_loop
  |-- model calls
  |-- SQL tools
  |-- knowledge tools
  |-- AutoHome WebFetch
  |
Observability
  |-- metrics
  |-- logs
  |-- traces
```

## 7. 参考资料

- FastAPI async/concurrency: https://fastapi.tiangolo.com/async/
- Celery task queue introduction: https://docs.celeryq.dev/en/stable/getting-started/introduction.html
- Celery worker concurrency: https://docs.celeryq.dev/en/stable/userguide/workers.html
- Redis Streams `XREADGROUP`: https://redis.io/docs/latest/commands/xreadgroup/
- Spring WebFlux: https://docs.spring.io/spring-framework/reference/web/webflux.html
- Netty user guide: https://netty.io/wiki/user-guide-for-4.x.html
- Vert.x Core: https://vertx.io/docs/vertx-core/java/
- Akka Actor Systems: https://doc.akka.io/libraries/akka-core/current/general/actor-systems.html
- Confluent Kafka consumer groups: https://docs.confluent.io/platform/current/clients/consumer.html

