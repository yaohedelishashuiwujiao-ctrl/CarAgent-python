# 4. Model Concurrency Scheduling

```mermaid
flowchart TD
    API[Job API] --> LIMIT[Admission limits<br/>max pending 5000<br/>per user 20]
    LIMIT --> QUEUES[Per queue_key queues]
    QUEUES --> DRR[DRR scheduler<br/>base quantum 4<br/>max credit 32<br/>tick 100ms]
    DRR --> SESSION{Session already running?}
    SESSION -- yes --> DEFER[Defer queue key]
    DEFER --> DRR
    SESSION -- no --> LOCK[Acquire session lock<br/>TTL 120s]
    LOCK --> DISPATCH[Dispatch lease<br/>TTL 30s<br/>attempt max 3]
    DISPATCH --> LOCALQ[Local dispatch queue<br/>size max(worker*2,16)]
    LOCALQ --> WORKERS[Worker pool<br/>default 8 workers]

    WORKERS --> MODELSEM[Model semaphore<br/>AGENT_MODEL_CONCURRENCY_ARK<br/>default 8]
    MODELSEM --> PROVIDER[Provider call<br/>Ark/OpenAI-compatible/Anthropic/GLM/Minimax]
    PROVIDER --> STREAM[SSE text/tool events<br/>persisted job events]

    PROVIDER --> TOOLUSES{Tool calls returned?}
    TOOLUSES -- no --> FINAL[Final response]
    TOOLUSES -- yes --> TS[ToolCallScheduler]

    TS --> PREFLIGHT[Preflight<br/>schema + permission + data scope<br/>dependency health + dedupe]
    PREFLIGHT --> SAFE{All sibling calls<br/>read-only + parallel + idempotent + no side effect?}
    SAFE -- yes --> TP[Parallel dependency layer<br/>max CLAWD_MAX_PARALLEL_TOOLS<br/>default 4, cap 16]
    SAFE -- no --> SEQ[Sequential dispatch]

    TP --> RUNTIMEPOOLS[Runtime tool resource pools]
    SEQ --> RUNTIMEPOOLS
    RUNTIMEPOOLS --> SQLP[sql pool<br/>runtime default 8 workers<br/>backend semaphore 16]
    RUNTIMEPOOLS --> WEBP[web pool<br/>runtime default 8 workers]
    RUNTIMEPOOLS --> KP[knowledge pool<br/>runtime default 8 workers]
    RUNTIMEPOOLS --> AP[artifact pool<br/>runtime default 2 workers<br/>backend semaphore 2]
    RUNTIMEPOOLS --> GP[generic tool pool<br/>runtime default 16 workers<br/>backend semaphore 32]
```

## Backend Job Scheduling Parameters

| Parameter | Default |
|---|---:|
| `AGENT_MAX_PENDING_JOBS` | `5000` |
| `AGENT_MAX_PENDING_PER_USER` | `20` |
| `AGENT_WORKER_CONCURRENCY` | `8` |
| `AGENT_MODEL_CONCURRENCY_ARK` | same as worker concurrency, default `8` |
| `AGENT_SQL_CONCURRENCY` | `16` |
| `AGENT_TOOL_CONCURRENCY` | `32` |
| `AGENT_ARTIFACT_CONCURRENCY` | `2` |
| `AGENT_SCHEDULER_TICK_MS` | `100` |
| `AGENT_DRR_BASE_QUANTUM` | `4` |
| `AGENT_DRR_MAX_CREDIT` | `32` |
| `AGENT_JOB_MAX_ATTEMPTS` | `3` |
| `AGENT_JOB_DEFAULT_MAX_TURNS` | `24` |
| `AGENT_DISPATCH_LEASE_TTL_MS` | `30000` |
| `AGENT_SESSION_LOCK_TTL_MS` | `120000` |
| `AGENT_SCHEDULER_LEADER_TTL_MS` | `10000` |

## Runtime Tool Pool Parameters

| Pool | Default workers | Env override |
|---|---:|---|
| `sql` | `8` | `CLAWD_TOOL_POOL_SQL_WORKERS` |
| `web` | `8` | `CLAWD_TOOL_POOL_WEB_WORKERS` |
| `knowledge` | `8` | `CLAWD_TOOL_POOL_KNOWLEDGE_WORKERS` |
| `artifact` | `2` | `CLAWD_TOOL_POOL_ARTIFACT_WORKERS` |
| `tool` | `16` | `CLAWD_TOOL_POOL_TOOL_WORKERS` |
| queue capacity | same as workers | `CLAWD_TOOL_POOL_QUEUE_CAPACITY`, capped at `256` |

## Parallel Dispatch Rules

Tool calls are only parallelized when every sibling call in the same model turn satisfies all of these:

- tool exists and preflight can execute
- `is_read_only=true`
- `supports_parallel=true`
- `side_effect=none`
- `idempotent=true`
- no duplicate fingerprint in the run or current batch
- below the execution fuse
- not disabled by repeated failure or boundary violation

Results are committed back to the conversation in the model's original order, even when execution was parallel, so run-state and transcript order remain deterministic.

