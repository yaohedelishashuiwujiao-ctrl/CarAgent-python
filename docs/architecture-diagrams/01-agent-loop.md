# 1. Real Agent Loop

```mermaid
flowchart TD
    U[User / Frontend] --> API[Backend chat job API]
    API --> ADMIT[Job admission<br/>idempotency key<br/>principal snapshot<br/>role_ids / data_scope / allowed_tools]
    ADMIT --> ROUTE[Route estimate<br/>route / budget_class / model_tier / estimated_cost]
    ROUTE --> QUEUE[Fair job queue<br/>DRR credits + per-session exclusion]
    QUEUE --> LOCK[Session lock<br/>TTL 120s<br/>heartbeat every 10s default]
    LOCK --> PROXY[Runtime proxy worker]

    PROXY --> INIT[Agent Runtime init<br/>RouteDecision<br/>TaskRequirementState<br/>AgentRunState<br/>RunBudget]
    INIT --> CTX[Build context<br/>style prompt + workspace/git context<br/>route policy + citation policy<br/>execution policy + requirements]
    CTX --> TOOLS[Expose candidate tools<br/>primary stage first<br/>fallback stage after boundary/failure]
    TOOLS --> LLM[Model call<br/>provider-neutral messages<br/>tools schema<br/>optional tier model override]

    LLM --> DECIDE{Model returns tool_use?}
    DECIDE -- no --> CONTRACT{Requirement / output contract satisfied?}
    CONTRACT -- yes --> FINAL[Finalize answer<br/>citation repair<br/>claim validation<br/>metadata assembly]
    CONTRACT -- no --> REMIND[Contract reminder<br/>or completion recovery<br/>force eligible tool if supported]
    REMIND --> LLM

    DECIDE -- yes --> PARALLEL{Multiple independent<br/>read-only tool calls?}
    PARALLEL -- yes --> SCHED[ToolCallScheduler<br/>preflight + dedupe + parallel dispatch]
    PARALLEL -- no --> SERIAL[Sequential dispatch]
    SCHED --> RESULT[ToolResult]
    SERIAL --> RESULT

    RESULT --> OBS[Observation processing<br/>outcome_status / reason_code<br/>evidence extraction<br/>citation ids<br/>coverage boundary]
    OBS --> STATE[Update run memory<br/>requirements<br/>plan revision<br/>failed paths<br/>budget counters<br/>scheduler ledger]
    STATE --> STOP{Stop / replan?}
    STOP -- replan --> LLM
    STOP -- continue --> LLM
    STOP -- synthesize --> FINAL

    FINAL --> PERSIST[Persist job final state<br/>events / citations / claims<br/>context snapshots]
    PERSIST --> SSE[SSE / frontend delivery]
```

## Key Runtime Facts

| Area | Actual implementation |
|---|---|
| Main loop | `run_agent_loop()` performs route decision, context build, model call, tool handling, observation update, contract check, finalization. |
| Route-driven tool exposure | Non-general routes start with preferred tools; fallback tools are exposed after output contract unmet, capability mismatch, data coverage insufficient, dependency unhealthy, or permission boundary. |
| Completion gate | Final text is accepted only when `TaskRequirementState` and output contract are satisfied; artifact tasks require actual generated files. |
| Tool result protocol | Every tool result carries `outcome_status`, `reason_code`, `retryable`, diagnostics, and evidence/citation metadata when available. |
| Safety controls | Tool execution fuse defaults to `24`; duplicate tool calls/results are fingerprinted; repeated failures disable low-yield tools for the current run. |
| Planning | Multi-step tasks use model-authored `TodoWrite`; runtime stores plan revision and progress but does not hard-code a domain workflow. |
| Citation behavior | Evidence is registered into numbered citations; final answers are repaired if citation ids are invalid or structured SQL values are underused. |

## Interview-Safe Summary

The project implements a production-style agent loop: route first, expose the smallest useful tool surface, call the model, execute eligible independent read-only tools in parallel, feed compact observations back, update task state and budgets, then only finalize after the output contract and evidence requirements are satisfied.

