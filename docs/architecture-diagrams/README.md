# SubjectsAgent Architecture Diagrams

These diagrams describe the actual implementation in this repository, not an aspirational design.
They follow the same Mermaid-first style as [`shareAI-lab/learn-claude-code`](https://github.com/shareAI-lab/learn-claude-code), but the nodes and parameters
come from the current backend, agent runtime, and RAG service code.

## Diagrams

1. [Agent Loop](./01-agent-loop.md)
2. [Agentic RAG Pipeline](./02-rag-pipeline.md)
3. [Model Routing Strategy](./03-model-routing-strategy.md)
4. [Model Concurrency Scheduling](./04-model-concurrency-scheduling.md)
5. [Memory Design](./05-memory-design.md)

## Source Anchors

- Agent loop: `agent_runtime/src/tool_system/agent_loop.py`
- Routing: `agent_runtime/src/routing_decision.py`, `agent_runtime/src/task_router.py`, `backend/app/services/agent_routing.py`
- Scheduling: `backend/app/services/agent_jobs.py`, `agent_runtime/src/tool_system/scheduler.py`, `agent_runtime/src/tool_system/execution.py`
- RAG: `backend/app/services/rag.py`, `docs/AGENT_KNOWLEDGE_TOOLS.md`, `scripts/evaluate_current_rag.py`
- Memory/context: `agent_runtime/src/tool_system/context.py`, `agent_runtime/src/tool_system/run_state.py`, `agent_runtime/src/context_system/budget.py`, `backend/app/services/agent_job_persistence.py`
