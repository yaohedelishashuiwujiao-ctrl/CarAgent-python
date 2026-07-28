# Agent Concurrency Design

## Goal

Support at least 2000 simultaneous users connected to the frontend and asking questions without letting model calls, tool calls, SQL connections, or in-process session state collapse under unbounded concurrency.

The system should not run 2000 full agent loops at the same instant. It should accept 2000 users, admit work through a scheduler, stream progress from durable events, and execute only the amount of model/tool work the downstream services can sustain.

## Current Bottlenecks

- `agent_runtime/web_app.py` uses `ThreadingHTTPServer`; every `/api/chat_stream` request occupies a Python thread until the agent run ends.
- `backend/app/routers/agent_runtime.py` holds a blocking `requests.post(..., stream=True)` connection for the full stream.
- Agent loops call the model synchronously and dispatch tools inline.
- SQL tools open short-lived connections per call instead of using a bounded pool.
- Session and REPL state are in process memory, so multiple workers cannot safely share state.

The runtime guardrails now limit active in-process agent runs with `CLAWD_MAX_CONCURRENT_AGENT_RUNS`, reject excess requests with HTTP 429, and reject concurrent mutation of the same session with HTTP 409. This prevents meltdown, but it is not the final 2000-user architecture.

## Target Architecture

Split the system into four planes:

1. Access plane
   - Async HTTP/SSE or WebSocket service.
   - Handles at least 2000 mostly idle client connections.
   - Does not execute agent loops directly.

2. Admission and scheduler plane
   - Creates a `chat_job` with idempotency key.
   - Applies per-user, per-session, per-tenant, and global quotas.
   - Pushes accepted jobs into a durable queue.

3. Worker plane
   - Runs bounded agent workers.
   - Uses separate semaphores for model calls, SQL calls, tool execution, and artifact generation.
   - Publishes progress events to Redis Streams, Kafka, or a database event table.

4. State plane
   - Stores jobs, session messages, tool traces, citations, cancellation state, and final answers in Redis plus SQL, or SQL only for the first production version.
   - Keeps in-process caches optional and disposable.

## Scheduling Algorithm

Use weighted fair queueing with resource tokens:

1. On request, compute a stable queue key:
   - `tenant_id:user_id` for fairness.
   - `session_id` for same-session serialization.

2. Admission control:
   - Reject if global queue length exceeds `MAX_PENDING_JOBS`.
   - Reject if user/session has too many pending jobs.
   - Return `202 Accepted` with `job_id`, `queue_position`, and event-stream URL.

3. Fair dispatch:
   - Maintain one FIFO subqueue per queue key.
   - Select jobs by deficit round robin:
     - Each key earns `weight` credits per scheduler tick.
     - A job costs estimated work units.
     - This prevents one user from consuming all workers.

4. Resource acquisition before execution:
   - `session_lock(session_id)` to serialize a conversation.
   - `model_tokens` semaphore sized from provider QPS and average latency.
   - `tool_tokens` semaphore for Python tool execution.
   - `sql_tokens` semaphore backed by SQL pool size.
   - Optional `artifact_tokens` for PPT/chart generation.

5. Adaptive concurrency:
   - Track provider 429s, latency p95, tool error rate, and queue wait.
   - Decrease model concurrency when 429/error/latency rises.
   - Increase slowly when p95 and error rates are stable.

Pseudo-code:

```text
while scheduler_running:
  key = fair_queue.pick_next_ready_key()
  job = queues[key].peek()
  if not job:
    continue
  if not session_lock.try_acquire(job.session_id):
    fair_queue.defer(key)
    continue
  if not resources.try_acquire(job.estimated_cost):
    session_lock.release(job.session_id)
    fair_queue.defer(key)
    continue
  queues[key].pop()
  worker_pool.submit(job)
```

## Capacity Model

For provider-bound workloads:

```text
model_concurrency = floor(provider_qps * average_model_latency_seconds / model_calls_per_job)
```

Example:

- Provider allows 120 requests/minute, or 2 QPS.
- Average model call latency is 8 seconds.
- Average job uses 2 model calls.
- Sustainable active jobs: `floor(2 * 8 / 2) = 8`.

This means 2000 users can be connected and queued, but only a controlled number should actively consume the model. More active jobs require more provider quota and more worker replicas, not just more web threads.

## API Shape

Recommended production flow:

- `POST /api/agent/chat_jobs`
  - Creates job, returns `202`, `job_id`, queue metadata.

- `GET /api/agent/chat_jobs/{job_id}/events`
  - SSE stream for telemetry, tool events, text deltas, final answer, and errors.

- `POST /api/agent/chat_jobs/{job_id}/cancel`
  - Marks job canceled; workers check before each model/tool call.

- `GET /api/agent/chat_jobs/{job_id}`
  - Returns current job state for reconnects.

## Tool-Call Optimization

Direct entity-attribute questions should avoid schema discovery unless needed:

- First turn exposes only `SubjectsAttributeLookup` for simple vehicle parameter questions.
- If lookup returns rows, the next turn exposes no tools, forcing final synthesis from the gathered evidence.
- If lookup returns zero rows or the task is comparison/aggregation/analysis, SQL and knowledge tools remain available.

This is task-shape based and not tied to specific vehicle names, brands, or attributes.

## Rollout Plan

1. Done now:
   - Disable unbounded WebSearch by default.
   - Restrict WebFetch to allowed intranet domains.
   - Add `SubjectsAttributeLookup`.
   - Add staged direct lookup and post-hit tool convergence.
   - Add in-process concurrency guardrails and same-session locking.

2. Next:
   - Replace blocking backend proxy with async streaming.
   - Add persistent `chat_jobs` and `chat_job_events`.
   - Move agent execution to worker processes.
   - Add Redis or SQL-backed queue with fair scheduling.

3. Scale-out:
   - Run multiple worker replicas.
   - Use distributed session locks.
   - Add SQL connection pooling and provider-specific adaptive concurrency.
   - Load test with 2000 connected clients and realistic active-question ratios.
