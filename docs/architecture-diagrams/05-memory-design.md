# 5. Actual Memory Design

```mermaid
flowchart TD
    REQ[User request + session_id] --> SNAP[Backend job snapshot<br/>tenant_id / user_id<br/>role_ids / data_scope<br/>allowed_tools / auth version]
    SNAP --> CONV[Conversation messages<br/>current user message<br/>assistant/tool turns]

    CONV --> BUILDER[Context builder<br/>workspace snapshot<br/>git snapshot<br/>project instructions]
    CONV --> BUDGET[Context budgeter<br/>window 120k tokens<br/>reserved output 8k<br/>soft 72%<br/>hard 90%]
    BUDGET --> EXT[Externalize old tool results<br/>keep recent 3<br/>sha marker + citation ids]
    BUDGET --> TRIM[Atomic trim<br/>drop complete tool-call/tool-result units<br/>insert context_snapshot/v1]

    CONV --> TOOLCTX[ToolContext memory<br/>cwd/workspace<br/>permission context<br/>read file fingerprints<br/>todos/tasks<br/>audit events<br/>runtime_state<br/>cancellation event]
    TOOLCTX --> RUNSTATE[AgentRunState<br/>goal / obligations<br/>plan + revision<br/>action counters<br/>failed paths<br/>progress events<br/>evidence ledger capped at 40]
    TOOLCTX --> RUNBUDGET[RunBudget<br/>token usage<br/>model turns<br/>tool requested/dispatched/rejected<br/>low-yield actions]
    TOOLCTX --> CONTRACT[TaskRequirementState<br/>evidence requirements<br/>artifact/output contract<br/>blocking reasons]

    RUNSTATE --> PROMPT[Run-state prompt injected into model turns]
    RUNBUDGET --> PROMPT
    CONTRACT --> PROMPT

    TOOLRESULT[Tool results] --> EVID[Evidence registry<br/>citation ids<br/>evidence_hash<br/>source type / metadata]
    EVID --> RUNSTATE
    EVID --> FINALMETA[Final metadata]
    FINALMETA --> PERSIST[Persistence layer<br/>agent_chat_jobs<br/>agent events<br/>agent_evidence_snapshot<br/>agent_claim<br/>agent_context_snapshot]
    PERSIST --> RESUME[Resume / audit / SSE replay]
```

## What Counts As Memory In This Project

| Memory layer | Purpose | Implementation |
|---|---|---|
| Conversation memory | Keeps current turn history and tool observations for the model. | `Conversation`, OpenAI-format messages, compacted before model calls. |
| Runtime context memory | Stores operational state needed by tools. | `ToolContext`: permissions, cwd, file read fingerprints, todos, tasks, audit events, runtime state, cancellation. |
| Plan memory | Persists the model-authored execution plan across turns. | `AgentRunState.plan`, `plan_revision`, `TodoWrite` updates. |
| Progress memory | Prevents loops and repeated bad actions. | action count, consecutive failures, no-progress count, failed path fingerprints, duplicate result fingerprints. |
| Evidence memory | Keeps a compact durable ledger for grounding and citation repair. | `AgentRunState.evidence_ledger`, capped at `40`; final metadata keeps full citations. |
| Budget memory | Tracks cost and low-yield behavior. | `RunBudget`: input/output tokens, model turns, scheduler ledger, low-yield actions. |
| Contract memory | Tracks whether the task is actually complete. | `TaskRequirementState` + output contract for artifact/structured outputs. |
| Persistence memory | Supports audit, resume, status polling, and SSE replay. | job/event persistence plus evidence, claim, and context snapshot tables. |

## Context Compaction Defaults

| Parameter | Default |
|---|---:|
| `CLAWD_CONTEXT_WINDOW_TOKENS` | `120000` |
| `CLAWD_CONTEXT_RESERVED_OUTPUT_TOKENS` | `8000` |
| `CLAWD_CONTEXT_SOFT_RATIO` | `0.72` |
| `CLAWD_CONTEXT_HARD_RATIO` | `0.90` |
| Recent tool results kept verbatim | `3` |
| Evidence ledger retained in run state | last `40` evidence items |

## Boundary To State Clearly

This project does not implement a user-profile long-term memory or a separate vectorized personal memory store. The actual memory design is operational: conversation state, compacted context, model-authored plan state, tool/evidence ledger, task contract state, and persisted job/audit snapshots.

