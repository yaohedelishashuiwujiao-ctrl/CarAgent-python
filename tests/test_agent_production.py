from __future__ import annotations

import asyncio
import os
import unittest
from unittest.mock import patch

from agent_runtime.src.context_system.budget import prepare_messages_with_budget
from agent_runtime.src.runtime_auth import RuntimeAuthError, verify_runtime_authorization
from agent_runtime.src.tool_system.context import ToolContext
from agent_runtime.src.tool_system.protocol import ToolCall, ToolResult
from agent_runtime.src.tool_system.registry import ToolRegistry, ToolSpec
from agent_runtime.src.tool_system.defaults import build_default_registry
from agent_runtime.src.tool_system.tools.subjects_sql import _generic_sql_scope_error
from backend.app.security import Principal, issue_api_token, issue_runtime_token, scope_session_id, verify_token
from backend.app.config import settings
from backend.app.services.rag import RagChunk, RagService
from scripts.agent_release_gate import DEFAULT_THRESHOLDS, evaluate_gate
from backend.app.services.agent_job_persistence import MemoryAgentJobPersistence
from backend.app.services.agent_jobs import AgentJobService
from backend.app.services.agent_routing import estimate_agent_job_route
from backend.app.services.agent_job_dispatch import DispatchMessage
from backend.app.services.agent_jobs_types import AgentJob, JobStatus, ensure_job_transition


class _ReadTool:
    def spec(self) -> ToolSpec:
        return ToolSpec(name="AllowedRead", description="test", input_schema={"type": "object"}, is_read_only=True)

    def run(self, tool_input: dict, context: ToolContext) -> ToolResult:
        return ToolResult(name="AllowedRead", output={"ok": True})


class _FakeSessionLocks:
    def __init__(self) -> None:
        self.released: list[tuple[str, str]] = []

    async def release(self, session_id: str, owner_id: str) -> None:
        self.released.append((session_id, owner_id))


class _OkResponse:
    def raise_for_status(self) -> None:
        return None


class ProductionFoundationTest(unittest.TestCase):
    def test_runtime_token_binds_job_session_and_identity(self) -> None:
        principal = Principal(
            tenant_id="tenant-a",
            user_id="user-a",
            role_ids=("engineer",),
            data_scope={"systems": ["brake"]},
            allowed_tools=("KnowledgeSearch",),
        )
        token = issue_runtime_token(job_id="job-1", session_id="session-1", principal=principal)
        claims = verify_runtime_authorization(f"Bearer {token}", session_id="session-1")
        self.assertEqual(claims["tenant_id"], "tenant-a")
        self.assertEqual(claims["data_scope"], {"systems": ["brake"]})
        with self.assertRaises(RuntimeAuthError):
            verify_runtime_authorization(f"Bearer {token}", session_id="another-session")

    def test_api_and_runtime_tokens_use_distinct_audiences(self) -> None:
        principal = Principal(tenant_id="t", user_id="u")
        api_token = issue_api_token(principal)
        claims = verify_token(f"Bearer {api_token}", audience=settings.api_token_audience)
        self.assertEqual(claims["user_id"], "u")
        with self.assertRaises(Exception):
            verify_token(f"Bearer {api_token}", audience="subjects-agent-runtime")

    def test_session_ids_are_namespaced_per_actor(self) -> None:
        first = scope_session_id("tenant", "user-a", "session")
        second = scope_session_id("tenant", "user-b", "session")
        self.assertNotEqual(first, second)
        self.assertTrue(first.endswith(":session"))

    def test_idempotency_returns_one_job(self) -> None:
        async def scenario() -> None:
            service = AgentJobService(persistence=MemoryAgentJobPersistence())
            first, second = await asyncio.gather(
                service.create_job(prompt="compare", session_id="s1", tenant_id="t1", user_id="u1", idempotency_key="same"),
                service.create_job(prompt="compare", session_id="s1", tenant_id="t1", user_id="u1", idempotency_key="same"),
            )
            self.assertEqual(first.id, second.id)
            self.assertEqual(service._pending_total, 1)

        asyncio.run(scenario())

    def test_job_cost_uses_route_budget_estimate(self) -> None:
        with patch.dict(os.environ, {"AGENT_JOB_BROKER": "memory"}):
            service = AgentJobService(persistence=MemoryAgentJobPersistence())

        self.assertEqual(service._estimate_cost("帮我生成一份竞品分析PPT"), 8)
        self.assertEqual(service._estimate_cost("解释一下底盘平台化是什么意思"), 1)
        self.assertEqual(service._estimate_cost("查询小鹏X9轴距"), 1)
        self.assertEqual(service._estimate_cost("帮我调研MPV市场趋势"), 4)

    def test_job_proxy_stream_has_no_read_timeout_by_default(self) -> None:
        with patch.dict(os.environ, {"AGENT_JOB_BROKER": "memory", "AGENT_JOB_UPSTREAM_READ_TIMEOUT_SECONDS": ""}):
            service = AgentJobService(persistence=MemoryAgentJobPersistence())

        self.assertIsNone(service.upstream_read_timeout_seconds)

    def test_job_proxy_stream_timeout_can_be_overridden(self) -> None:
        with patch.dict(os.environ, {"AGENT_JOB_BROKER": "memory", "AGENT_JOB_UPSTREAM_READ_TIMEOUT_SECONDS": "30"}):
            service = AgentJobService(persistence=MemoryAgentJobPersistence())

        self.assertEqual(service.upstream_read_timeout_seconds, 30.0)

    def test_job_route_estimate_matches_runtime_l0_routing(self) -> None:
        estimate = estimate_agent_job_route("查询平台数据中小米SU7的轴距，只回答数值、单位和来源。")

        self.assertEqual(estimate.route, "vehicle_spec")
        self.assertEqual(estimate.budget_class, "lookup")
        self.assertEqual(estimate.model_tier, "cheap")
        self.assertEqual(estimate.estimated_cost, 1)

    def test_illegal_terminal_transition_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            ensure_job_transition(JobStatus.SUCCEEDED, JobStatus.RUNNING)

    def test_unsettled_dispatch_is_requeued_before_ack(self) -> None:
        async def scenario() -> None:
            with patch.dict(os.environ, {"AGENT_JOB_BROKER": "memory"}):
                service = AgentJobService(persistence=MemoryAgentJobPersistence())
            service.session_locks = _FakeSessionLocks()
            job = AgentJob(
                id="job-lost",
                tenant_id="tenant",
                user_id="user",
                session_id="session",
                prompt="test",
                queue_key="tenant:user",
                estimated_cost=1,
                max_turns=2,
                status=JobStatus.ADMITTED,
                attempt_count=1,
                execution_token="token-1",
            )
            service._jobs[job.id] = job
            service._running_sessions.add(job.session_id)
            service._pending_total = 1

            settled = await service._settle_dispatch_message(
                DispatchMessage("1-0", job.id, execution_token="token-1", queue_key=job.queue_key),
                "worker-1",
            )

            self.assertTrue(settled)
            self.assertEqual(job.status, JobStatus.QUEUED)
            self.assertIsNone(job.execution_token)
            self.assertEqual(list(service._queues[job.queue_key]), [job.id])
            self.assertIn(job.queue_key, service._active_key_set)
            self.assertEqual(service._dispatch_messages_retried, 1)
            self.assertEqual(service.session_locks.released, [(job.session_id, job.id)])

        asyncio.run(scenario())

    def test_worker_exception_requeues_running_job(self) -> None:
        async def scenario() -> None:
            with patch.dict(os.environ, {"AGENT_JOB_BROKER": "memory"}):
                service = AgentJobService(persistence=MemoryAgentJobPersistence())
            service.session_locks = _FakeSessionLocks()
            job = AgentJob(
                id="job-crash",
                tenant_id="tenant",
                user_id="user",
                session_id="session",
                prompt="test",
                queue_key="tenant:user",
                estimated_cost=1,
                max_turns=2,
                status=JobStatus.RUNNING,
                attempt_count=1,
                execution_token="token-1",
                assigned_worker_id="worker-1",
            )
            service._jobs[job.id] = job
            service._running_sessions.add(job.session_id)
            service._pending_total = 1

            settled = await service._retry_dispatch_message(
                DispatchMessage("1-0", job.id, execution_token="token-1", queue_key=job.queue_key),
                worker_id="worker-1",
                reason="worker execution failed: test",
                retry_running=True,
            )

            self.assertTrue(settled)
            self.assertEqual(job.status, JobStatus.QUEUED)
            self.assertIsNone(job.assigned_worker_id)
            self.assertEqual(list(service._queues[job.queue_key]), [job.id])

        asyncio.run(scenario())

    def test_running_job_cancel_signals_runtime_before_transport_close(self) -> None:
        async def scenario() -> None:
            with patch.dict(os.environ, {"AGENT_JOB_BROKER": "memory"}):
                service = AgentJobService(persistence=MemoryAgentJobPersistence())
            job = AgentJob(
                id="job-cancel-signal",
                tenant_id="tenant",
                user_id="user",
                session_id="session",
                prompt="test",
                queue_key="tenant:user",
                estimated_cost=1,
                max_turns=4,
                status=JobStatus.RUNNING,
                execution_token="token",
            )
            service._jobs[job.id] = job
            with patch("backend.app.services.agent_jobs.requests.post", return_value=_OkResponse()) as post:
                await service.cancel_job(job.id)

            self.assertEqual(job.status, JobStatus.CANCEL_REQUESTED)
            self.assertEqual(post.call_args.args[0], f"{service.agent_base_url}/api/cancel")
            self.assertEqual(post.call_args.kwargs["json"]["job_id"], job.id)
            service._proxy_executor.shutdown(wait=True)
            service._persistence_executor.shutdown(wait=True)
            service._control_executor.shutdown(wait=True)

        asyncio.run(scenario())

    def test_unmet_runtime_task_contract_fails_job(self) -> None:
        async def scenario() -> None:
            with patch.dict(os.environ, {"AGENT_JOB_BROKER": "memory", "AGENT_JOB_EXECUTOR": "proxy"}):
                service = AgentJobService(persistence=MemoryAgentJobPersistence())
            service.session_locks = _FakeSessionLocks()
            job = AgentJob(
                id="job-contract",
                tenant_id="tenant",
                user_id="user",
                session_id="session",
                prompt="生成6页PPT",
                queue_key="tenant:user",
                estimated_cost=1,
                max_turns=4,
                status=JobStatus.ADMITTED,
                attempt_count=1,
                execution_token="token-1",
            )
            service._jobs[job.id] = job
            service._pending_total = 1
            service._pending_by_user[(job.tenant_id, job.user_id)] = 1
            service._execute_proxy_blocking = lambda *_args: (
                "只返回了文字",
                {"input_tokens": 10, "output_tokens": 5},
                0,
                {
                    "output_contract_status": "unmet",
                    "task_contract_status": "unmet",
                    "requirements": [{"id": "artifact:pptx", "status": "open"}],
                },
            )

            await service._execute_job(job.id, "worker-1", "token-1")

            self.assertEqual(job.status, JobStatus.FAILED)
            self.assertEqual(job.error_message, "required task contract was not satisfied")
            self.assertEqual(job.final_metadata["output_contract_status"], "unmet")
            self.assertEqual(service._pending_total, 0)
            self.assertTrue(any(event.event_type == "failed" for event in service._events[job.id]))
            self.assertFalse(any(event.event_type == "final" for event in service._events[job.id]))
            service._proxy_executor.shutdown(wait=True)
            service._persistence_executor.shutdown(wait=True)

        asyncio.run(scenario())

    def test_satisfied_task_contract_publishes_one_final_with_metadata(self) -> None:
        async def scenario() -> None:
            with patch.dict(os.environ, {"AGENT_JOB_BROKER": "memory", "AGENT_JOB_EXECUTOR": "proxy"}):
                service = AgentJobService(persistence=MemoryAgentJobPersistence())
            service.session_locks = _FakeSessionLocks()
            job = AgentJob(
                id="job-contract-ok",
                tenant_id="tenant",
                user_id="user",
                session_id="session-ok",
                prompt="查询轴距",
                queue_key="tenant:user",
                estimated_cost=1,
                max_turns=4,
                status=JobStatus.ADMITTED,
                attempt_count=1,
                execution_token="token-ok",
            )
            service._jobs[job.id] = job
            service._pending_total = 1
            service._pending_by_user[(job.tenant_id, job.user_id)] = 1
            service._execute_proxy_blocking = lambda *_args: (
                "轴距为3000mm [1]",
                {"input_tokens": 10, "output_tokens": 5},
                1,
                {
                    "output_contract_status": "not_required",
                    "task_contract_status": "satisfied",
                    "requirements": [{"id": "evidence:structured_fact", "status": "satisfied"}],
                    "citations": [{"citation_id": 1, "source_type": "structured_data"}],
                },
            )

            await service._execute_job(job.id, "worker-1", "token-ok")

            self.assertEqual(job.status, JobStatus.SUCCEEDED)
            final_events = [event for event in service._events[job.id] if event.event_type == "final"]
            self.assertEqual(len(final_events), 1)
            self.assertEqual(final_events[0].payload["task_contract_status"], "satisfied")
            self.assertEqual(final_events[0].payload["citations"][0]["citation_id"], 1)
            service._proxy_executor.shutdown(wait=True)
            service._persistence_executor.shutdown(wait=True)

        asyncio.run(scenario())

    def test_distributed_read_cannot_regress_terminal_job(self) -> None:
        persistence = MemoryAgentJobPersistence()
        persisted = AgentJob(
            id="job-state",
            tenant_id="tenant",
            user_id="user",
            session_id="session",
            prompt="test",
            queue_key="tenant:user",
            estimated_cost=1,
            max_turns=2,
            status=JobStatus.ADMITTED,
            attempt_count=1,
            execution_token="token-1",
            fencing_token=1,
        )
        persistence.save_job(persisted)
        current = AgentJob(**{**persisted.__dict__, "status": JobStatus.SUCCEEDED, "final_text": "done"})

        selected = AgentJobService._select_freshest_job(current, persistence.load_job(current.id))

        self.assertEqual(selected.status, JobStatus.SUCCEEDED)
        self.assertEqual(selected.final_text, "done")

    def test_tool_allowlist_is_enforced_and_audited(self) -> None:
        registry = ToolRegistry([_ReadTool()])
        context = ToolContext(workspace_root=".", allowed_tools=frozenset({"DifferentTool"}), job_id="job-1")
        result = registry.dispatch(ToolCall(name="AllowedRead", input={}), context)
        self.assertTrue(result.is_error)
        self.assertEqual(context.audit_events[-1]["authorization_decision"], "deny")

    def test_context_compaction_preserves_tool_pairs(self) -> None:
        messages: list[dict] = [{"role": "user", "content": "compare vehicles; retain 2800 mm and citation [7]"}]
        for index in range(10):
            messages.extend(
                [
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [{"id": f"call-{index}", "type": "function", "function": {"name": "Lookup", "arguments": "{}"}}],
                    },
                    {"role": "tool", "tool_call_id": f"call-{index}", "content": "x" * 5000 + " [7]"},
                ]
            )
        messages.append({"role": "user", "content": "give the final answer"})
        with patch.dict(
            os.environ,
            {
                "CLAWD_CONTEXT_WINDOW_TOKENS": "8192",
                "CLAWD_CONTEXT_RESERVED_OUTPUT_TOKENS": "1024",
                "CLAWD_CONTEXT_SOFT_RATIO": "0.50",
                "CLAWD_CONTEXT_HARD_RATIO": "0.70",
            },
        ):
            result = prepare_messages_with_budget(messages)
        self.assertTrue(result.compacted)
        call_ids = {
            call["id"]
            for message in result.messages
            for call in message.get("tool_calls", [])
        }
        result_ids = {message.get("tool_call_id") for message in result.messages if message.get("role") == "tool"}
        self.assertEqual(call_ids, result_ids)

    def test_production_registry_excludes_shell_and_file_writes(self) -> None:
        with patch.dict(os.environ, {"APP_ENV": "production", "CLAWD_TOOL_PROFILE": "production"}):
            names = {spec.name for spec in build_default_registry(include_user_tools=True).list_specs()}
        self.assertNotIn("Bash", names)
        self.assertNotIn("Write", names)
        self.assertIn("KnowledgeSearch", names)
        self.assertIn("ToolSearch", names)
        self.assertIn("SubjectsDataCatalogSearch", names)

    def test_restrictive_sql_scope_requires_business_tool(self) -> None:
        context = ToolContext(
            workspace_root=".",
            data_scope={"vehicle_ids": [1, 2]},
            role_ids=("engineer",),
        )
        error = _generic_sql_scope_error("SELECT * FROM vehicle_instance", {"vehicle_instance"}, context)
        self.assertIn("business query tool", error or "")

    def test_rag_scope_filters_documents(self) -> None:
        chunk = RagChunk(
            chunk_id="c1",
            document_id="d1",
            dataset_id="manual-corpus",
            title="manual",
            text="text",
            source="source",
            metadata={"brand": "蔚来", "source_type": "manual_pdf"},
            ordinal=0,
        )
        self.assertTrue(RagService._matches_data_scope(chunk, {"document_ids": ["d1"]}))
        self.assertFalse(RagService._matches_data_scope(chunk, {"document_ids": ["d2"]}))

    def test_release_gate_fails_closed_on_missing_or_unsafe_metrics(self) -> None:
        metrics = {name: rule.get("min", rule.get("max", 0)) for name, rule in DEFAULT_THRESHOLDS.items()}
        self.assertEqual(evaluate_gate(metrics), [])
        metrics["authorization_violation_count"] = 1
        failures = evaluate_gate(metrics)
        self.assertTrue(any("authorization_violation_count" in item for item in failures))
        del metrics["citation_validity"]
        self.assertTrue(any("missing required metric: citation_validity" in item for item in evaluate_gate(metrics)))


if __name__ == "__main__":
    unittest.main()
