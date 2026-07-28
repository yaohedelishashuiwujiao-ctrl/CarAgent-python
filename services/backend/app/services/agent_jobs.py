from __future__ import annotations

import asyncio
import json
import os
import threading
import time
import uuid
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from dataclasses import replace
from typing import Any, Deque

import requests

from backend.app.config import settings
from backend.app.security import Principal, issue_runtime_token, scope_session_id
from backend.app.services.agent_job_dispatch import (
    AgentJobDispatchBackend,
    DispatchMessage,
    RedisStreamAgentJobDispatchBackend,
    build_agent_job_dispatch_backend,
)
from backend.app.services.agent_job_locks import AgentSessionLockBackend, build_agent_session_lock_backend
from backend.app.services.agent_job_locks import AgentSchedulerLeaderBackend, build_agent_scheduler_leader_backend
from backend.app.services.agent_job_persistence import AgentJobPersistence, build_agent_job_persistence
from backend.app.services.agent_jobs_types import AgentEvent, AgentJob, JobStatus, ensure_job_transition
from backend.app.services.agent_routing import estimate_agent_job_route


class AgentJobService:
    """In-process M1 job runtime with DRR scheduling and bounded workers.

    The class intentionally keeps storage behind methods so SQL/Redis can
    replace the dictionaries without changing the HTTP contract.
    """

    def __init__(self, *, persistence: AgentJobPersistence | None = None) -> None:
        self.max_pending_jobs = int(os.getenv("AGENT_MAX_PENDING_JOBS", "5000"))
        self.max_pending_per_user = int(os.getenv("AGENT_MAX_PENDING_PER_USER", "20"))
        self.worker_concurrency = int(os.getenv("AGENT_WORKER_CONCURRENCY", "8"))
        self.model_concurrency = int(os.getenv("AGENT_MODEL_CONCURRENCY_ARK", str(self.worker_concurrency)))
        self.sql_concurrency = int(os.getenv("AGENT_SQL_CONCURRENCY", "16"))
        self.tool_concurrency = int(os.getenv("AGENT_TOOL_CONCURRENCY", "32"))
        self.artifact_concurrency = int(os.getenv("AGENT_ARTIFACT_CONCURRENCY", "2"))
        self.scheduler_tick_seconds = int(os.getenv("AGENT_SCHEDULER_TICK_MS", "100")) / 1000
        self.base_quantum = int(os.getenv("AGENT_DRR_BASE_QUANTUM", "4"))
        self.max_credit = int(os.getenv("AGENT_DRR_MAX_CREDIT", "32"))
        self.max_attempts = int(os.getenv("AGENT_JOB_MAX_ATTEMPTS", "3"))
        self.default_max_turns = int(os.getenv("AGENT_JOB_DEFAULT_MAX_TURNS", "24"))
        upstream_read_timeout = os.getenv("AGENT_JOB_UPSTREAM_READ_TIMEOUT_SECONDS", "").strip()
        self.upstream_read_timeout_seconds = float(upstream_read_timeout) if upstream_read_timeout else None
        self.dispatch_lease_ttl_ms = int(os.getenv("AGENT_DISPATCH_LEASE_TTL_MS", "30000"))
        self.session_lock_ttl_ms = int(os.getenv("AGENT_SESSION_LOCK_TTL_MS", "120000"))
        shortest_lease_ms = min(self.session_lock_ttl_ms, self.dispatch_lease_ttl_ms)
        self.session_lock_heartbeat_ms = max(int(shortest_lease_ms / 3), 1000)
        self.scheduler_leader_ttl_ms = int(os.getenv("AGENT_SCHEDULER_LEADER_TTL_MS", "10000"))
        # Job admission queues are currently process-local. A single global
        # scheduler leader can strand jobs accepted by non-leader API workers.
        # Keep the global leader opt-in until scheduling state itself is shared.
        self.scheduler_global_leader = os.getenv("AGENT_SCHEDULER_GLOBAL_LEADER", "false").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        self.executor = os.getenv("AGENT_JOB_EXECUTOR", "proxy")
        self.agent_base_url = (
            os.getenv("CLAWD_AGENT_WEB_URL")
            or os.getenv("AGENT_WEB_BASE_URL")
            or "http://127.0.0.1:7862"
        ).rstrip("/")
        self.job_role = os.getenv("AGENT_JOB_ROLE", "all").lower()
        self.persistence: AgentJobPersistence = persistence or build_agent_job_persistence()
        self.dispatch_backend: AgentJobDispatchBackend = build_agent_job_dispatch_backend()
        self.session_locks: AgentSessionLockBackend = build_agent_session_lock_backend()
        self.scheduler_leader: AgentSchedulerLeaderBackend = build_agent_scheduler_leader_backend()
        self._distributed_reads = isinstance(self.dispatch_backend, RedisStreamAgentJobDispatchBackend)
        self._instance_id = f"{os.uname().nodename}-{uuid.uuid4().hex[:8]}"

        self._lock = asyncio.Lock()
        self._jobs: dict[str, AgentJob] = {}
        self._events: dict[str, list[AgentEvent]] = defaultdict(list)
        self._subscribers: dict[str, set[asyncio.Queue[AgentEvent]]] = defaultdict(set)
        self._queues: dict[str, Deque[str]] = defaultdict(deque)
        self._active_keys: Deque[str] = deque()
        self._active_key_set: set[str] = set()
        self._credits: dict[str, int] = defaultdict(int)
        self._running_sessions: set[str] = set()
        self._pending_total = 0
        self._pending_by_user: dict[tuple[str, str], int] = defaultdict(int)
        self._persistence_queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue(maxsize=10000)
        self._pending_job_persistence_ids: set[str] = set()
        # A single broker reader fans dispatch messages into local workers. Running one
        # blocking Redis XREADGROUP per worker exhausts Redis connections at high worker counts.
        self._dispatch_queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=max(self.worker_concurrency * 2, 16))
        self._model_sem = asyncio.Semaphore(self.model_concurrency)
        self._sql_sem = asyncio.Semaphore(self.sql_concurrency)
        self._tool_sem = asyncio.Semaphore(self.tool_concurrency)
        self._artifact_sem = asyncio.Semaphore(self.artifact_concurrency)
        self._started = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._tasks: list[asyncio.Task[Any]] = []
        self._persistence_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="agent-persist")
        self._proxy_executor = ThreadPoolExecutor(
            max_workers=self.worker_concurrency,
            thread_name_prefix="agent-proxy",
        )
        self._control_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="agent-control")
        self._active_proxy_lock = threading.Lock()
        self._active_proxy_responses: dict[str, requests.Response] = {}
        self._scheduler_is_leader = False
        self._last_stalled_recovery_at = 0.0
        self._dispatch_last_error: str | None = None
        self._dispatch_last_worker_error: str | None = None
        self._dispatch_messages_received = 0
        self._dispatch_messages_acked = 0
        self._dispatch_messages_retried = 0
        self._dispatch_worker_errors = 0

    async def start(self) -> None:
        if self._started:
            return
        self._loop = asyncio.get_running_loop()
        if self.job_role == "worker" and not isinstance(self.dispatch_backend, RedisStreamAgentJobDispatchBackend):
            raise RuntimeError("AGENT_JOB_ROLE=worker requires AGENT_JOB_BROKER=redis and REDIS_URL")
        if self.job_role != "worker":
            await self._restore_from_store()
        self._started = True
        self._tasks.append(asyncio.create_task(self._persistence_loop(), name="agent-job-persistence"))
        if self.job_role in {"all", "api"}:
            self._tasks.append(asyncio.create_task(self._scheduler_loop(), name="agent-job-scheduler"))
        if self.job_role in {"all", "worker"}:
            self._tasks.append(asyncio.create_task(self._dispatch_loop(), name="agent-job-dispatch"))
            for index in range(self.worker_concurrency):
                self._tasks.append(
                    asyncio.create_task(self._worker_loop(f"agent-worker-{index + 1}"), name=f"agent-worker-{index + 1}")
                )

    async def stop(self) -> None:
        try:
            await asyncio.wait_for(self._persistence_queue.join(), timeout=5)
        except asyncio.TimeoutError:
            pass
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._persistence_executor.shutdown(wait=False, cancel_futures=True)
        self._proxy_executor.shutdown(wait=False, cancel_futures=True)
        self._control_executor.shutdown(wait=False, cancel_futures=True)
        await self._release_scheduler_leader()
        await self.dispatch_backend.close()
        await self.session_locks.close()
        await self.scheduler_leader.close()
        self._started = False

    async def create_job(
        self,
        *,
        prompt: str,
        session_id: str,
        user_id: str = "anonymous",
        tenant_id: str = "default",
        max_turns: int | None = None,
        idempotency_key: str | None = None,
        role_ids: tuple[str, ...] = (),
        data_scope: dict[str, Any] | None = None,
        allowed_tools: tuple[str, ...] = (),
        trace_id: str | None = None,
    ) -> AgentJob:
        prompt = prompt.strip()
        if not prompt:
            raise ValueError("prompt is required")
        idempotency_key = (idempotency_key or "").strip() or None
        if idempotency_key and len(idempotency_key) > 128:
            raise ValueError("idempotency_key must not exceed 128 characters")
        session_id = scope_session_id(tenant_id, user_id, session_id)
        job_id = f"job_{uuid.uuid4().hex}"
        route_estimate = estimate_agent_job_route(prompt)
        cost = route_estimate.estimated_cost
        queue_key = f"{tenant_id}:{user_id}"
        async with self._lock:
            if idempotency_key:
                existing = await self._run_persist_result(
                    self.persistence.load_by_idempotency, tenant_id, user_id, idempotency_key
                )
                if existing is not None:
                    self._jobs[existing.id] = existing
                    return existing
            if self._pending_total >= self.max_pending_jobs:
                raise RuntimeError("agent job queue is full")
            user_key = (tenant_id, user_id)
            if self._pending_by_user[user_key] >= self.max_pending_per_user:
                raise RuntimeError("user has too many pending agent jobs")
            job = AgentJob(
                id=job_id,
                tenant_id=tenant_id,
                user_id=user_id,
                session_id=session_id,
                prompt=prompt,
                queue_key=queue_key,
                estimated_cost=cost,
                max_turns=max(1, min(max_turns or self.default_max_turns, 100)),
                trace_id=trace_id,
                idempotency_key=idempotency_key,
                role_ids_snapshot=tuple(role_ids),
                data_scope_snapshot=dict(data_scope or {}),
                allowed_tools_snapshot=tuple(allowed_tools) or settings.default_agent_tools,
            )
            stored = await self._run_persist_result(self.persistence.create_job, job)
            if stored.id != job.id:
                self._jobs[stored.id] = stored
                return stored
            self._jobs[job.id] = job
            self._pending_total += 1
            self._pending_by_user[user_key] += 1
            self._queues[queue_key].append(job.id)
            if queue_key not in self._active_key_set:
                self._active_keys.append(queue_key)
                self._active_key_set.add(queue_key)
            self._emit_locked(
                job.id,
                "queued",
                {
                    "job_id": job.id,
                    "queue_key": queue_key,
                    "estimated_cost": cost,
                    "route_estimate": route_estimate.as_dict(),
                },
            )
            return job

    async def get_job(self, job_id: str) -> AgentJob | None:
        async with self._lock:
            job = self._jobs.get(job_id)
        if job and not self._distributed_reads:
            return job
        latest = await asyncio.to_thread(self.persistence.load_job, job_id)
        if latest is None:
            return job
        async with self._lock:
            current = self._jobs.get(job_id)
            selected = self._select_freshest_job(current, latest)
            self._jobs[job_id] = selected
            return selected

    @staticmethod
    def _select_freshest_job(current: AgentJob | None, persisted: AgentJob) -> AgentJob:
        """Merge a distributed read without regressing the local state machine.

        SQL persistence is asynchronous, so a read can legitimately return an
        older state for the same fencing token. Higher fencing tokens always
        win. Within one fence, terminal and running states must not be replaced
        by queued/admitted snapshots that were written earlier.
        """
        if current is None:
            return persisted
        if persisted.fencing_token != current.fencing_token:
            return persisted if persisted.fencing_token > current.fencing_token else current

        terminal = {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.REJECTED}
        if current.status in terminal:
            return current
        if persisted.status in terminal:
            return persisted

        rank = {
            JobStatus.QUEUED: 0,
            JobStatus.ADMITTED: 1,
            JobStatus.RUNNING: 2,
            JobStatus.CANCEL_REQUESTED: 3,
        }
        current_rank = rank.get(current.status, 0)
        persisted_rank = rank.get(persisted.status, 0)
        if persisted_rank >= current_rank:
            return persisted

        # A lease recovery may intentionally move RUNNING/ADMITTED back to
        # QUEUED on the same fence. Accept it only after the local lease ended.
        if current.lease_expires_at is not None and current.lease_expires_at <= time.time():
            return persisted
        return current

    async def snapshot_job_statuses(self, job_ids: list[str]) -> dict[str, str]:
        async with self._lock:
            return {
                job_id: self._jobs[job_id].status.value
                for job_id in job_ids
                if job_id in self._jobs
            }

    async def get_events_after(self, job_id: str, after_seq: int = 0) -> list[AgentEvent]:
        async with self._lock:
            local_events = [event for event in self._events.get(job_id, []) if event.seq > after_seq]
        if not self._distributed_reads:
            return local_events
        persisted = await asyncio.to_thread(self.persistence.load_events_after, job_id, after_seq)
        merged: dict[int, AgentEvent] = {event.seq: event for event in local_events}
        for event in persisted:
            merged[event.seq] = event
        return [merged[seq] for seq in sorted(merged)]

    async def subscribe(self, job_id: str) -> asyncio.Queue[AgentEvent]:
        queue: asyncio.Queue[AgentEvent] = asyncio.Queue(maxsize=200)
        async with self._lock:
            self._subscribers[job_id].add(queue)
        return queue

    async def unsubscribe(self, job_id: str, queue: asyncio.Queue[AgentEvent]) -> None:
        async with self._lock:
            subscribers = self._subscribers.get(job_id)
            if subscribers:
                subscribers.discard(queue)

    async def cancel_job(self, job_id: str) -> AgentJob | None:
        close_upstream = False
        release_session = False
        async with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            if job.status in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.REJECTED}:
                return job
            if job.status == JobStatus.QUEUED:
                queue = self._queues.get(job.queue_key)
                if queue:
                    with suppress(ValueError):
                        queue.remove(job.id)
                self._mark_cancelled_locked(job)
                release_session = True
            else:
                self._transition_locked(job, JobStatus.CANCEL_REQUESTED)
                self._persist_job(job)
                self._emit_locked(job_id, "cancel_requested", {"job_id": job_id})
                close_upstream = True
        if close_upstream:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(self._control_executor, self._cancel_runtime_blocking, job)
        if release_session:
            with suppress(Exception):
                await self.session_locks.release(job.session_id, job.id)
        return job

    def _cancel_runtime_blocking(self, job: AgentJob) -> None:
        self._signal_runtime_cancel_blocking(job)
        self._close_active_proxy_response(job.id)

    def _signal_runtime_cancel_blocking(self, job: AgentJob) -> None:
        principal = Principal(
            tenant_id=job.tenant_id,
            user_id=job.user_id,
            role_ids=job.role_ids_snapshot,
            data_scope=job.data_scope_snapshot,
            allowed_tools=job.allowed_tools_snapshot,
            auth_method="job-cancellation",
        )
        token = issue_runtime_token(
            job_id=job.id,
            session_id=job.session_id,
            principal=principal,
            trace_id=job.trace_id,
        )
        try:
            response = requests.post(
                f"{self.agent_base_url}/api/cancel",
                json={"job_id": job.id, "session_id": job.session_id},
                headers={"Authorization": f"Bearer {token}", "X-Agent-Job-ID": job.id},
                timeout=(1, 2),
            )
            response.raise_for_status()
        except Exception as exc:
            # Closing the streaming response remains the transport-level fallback.
            self._dispatch_last_worker_error = f"runtime cancel signal failed: {type(exc).__name__}: {exc}"[:2000]

    async def stats(self) -> dict[str, Any]:
        async with self._lock:
            counts: dict[str, int] = defaultdict(int)
            for job in self._jobs.values():
                counts[job.status.value] += 1
            pending_total = self._pending_total
            active_queue_keys = len(self._active_key_set)
            persistence_backlog = self._persistence_queue.qsize()
        dispatch_backlog = await self.dispatch_backend.backlog()
        return {
            "jobs": dict(counts),
            "pending_total": pending_total,
            "active_queue_keys": active_queue_keys,
            "dispatch_backlog": dispatch_backlog,
            "worker_concurrency": self.worker_concurrency,
            "model_concurrency": self.model_concurrency,
            "sql_concurrency": self.sql_concurrency,
            "tool_concurrency": self.tool_concurrency,
            "artifact_concurrency": self.artifact_concurrency,
            "executor": self.executor,
            "job_role": self.job_role,
            "scheduler_is_leader": self._scheduler_leader_is_owner(),
            "scheduler_global_leader": self.scheduler_global_leader,
            "dispatch_backend": type(self.dispatch_backend).__name__,
            "local_dispatch_backlog": self._dispatch_queue.qsize(),
            "dispatch_last_error": self._dispatch_last_error,
            "dispatch_last_worker_error": self._dispatch_last_worker_error,
            "dispatch_messages_received": self._dispatch_messages_received,
            "dispatch_messages_acked": self._dispatch_messages_acked,
            "dispatch_messages_retried": self._dispatch_messages_retried,
            "dispatch_worker_errors": self._dispatch_worker_errors,
            "session_lock_backend": type(self.session_locks).__name__,
            "scheduler_leader_backend": type(self.scheduler_leader).__name__,
            "session_lock_ttl_ms": self.session_lock_ttl_ms,
            "heartbeat_interval_ms": self.session_lock_heartbeat_ms,
            "dispatch_lease_ttl_ms": self.dispatch_lease_ttl_ms,
            "max_attempts": self.max_attempts,
            "default_max_turns": self.default_max_turns,
            "upstream_read_timeout_seconds": self.upstream_read_timeout_seconds,
            "persistence": type(self.persistence).__name__,
            "persistence_backlog": persistence_backlog,
        }

    def serialize_job(self, job: AgentJob) -> dict[str, Any]:
        return {
            "job_id": job.id,
            "tenant_id": job.tenant_id,
            "user_id": job.user_id,
            "session_id": job.session_id,
            "status": job.status.value,
            "queue_key": job.queue_key,
            "estimated_cost": job.estimated_cost,
            "max_turns": job.max_turns,
            "trace_id": job.trace_id,
            "idempotency_key": job.idempotency_key,
            "auth_context_version": job.auth_context_version,
            "fencing_token": job.fencing_token,
            "created_at": job.created_at,
            "queued_at": job.queued_at,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
            "assigned_worker_id": job.assigned_worker_id,
            "error_message": job.error_message,
            "final_text": job.final_text,
            "final_metadata": job.final_metadata,
            "usage": {
                "input_tokens": job.input_tokens,
                "output_tokens": job.output_tokens,
                "tool_call_count": job.tool_call_count,
            },
        }

    def serialize_event(self, event: AgentEvent) -> dict[str, Any]:
        return {
            "seq": event.seq,
            "event_type": event.event_type,
            "payload": event.payload,
            "created_at": event.created_at,
        }

    async def _scheduler_loop(self) -> None:
        while True:
            try:
                if self.job_role not in {"all", "api"}:
                    await asyncio.sleep(self.scheduler_tick_seconds)
                    continue
                if not await self._ensure_scheduler_leader():
                    await asyncio.sleep(self.scheduler_tick_seconds)
                    continue
                now = time.time()
                if now - self._last_stalled_recovery_at >= 5.0:
                    await self._recover_stalled_jobs()
                    self._last_stalled_recovery_at = now
                if not await self._renew_scheduler_leader():
                    continue
                while len(self._running_sessions) < self.worker_concurrency:
                    if not await self._schedule_once():
                        break
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
            await asyncio.sleep(self.scheduler_tick_seconds)

    async def _ensure_scheduler_leader(self) -> bool:
        if self.job_role not in {"all", "api"}:
            return False
        if not self.scheduler_global_leader:
            self._scheduler_is_leader = True
            return True
        if self._scheduler_leader_is_owner():
            return True
        acquired = await self.scheduler_leader.acquire(self._instance_id, ttl_ms=self.scheduler_leader_ttl_ms)
        if acquired:
            self._scheduler_is_leader = True
        return acquired

    async def _renew_scheduler_leader(self) -> bool:
        if self.job_role not in {"all", "api"}:
            return False
        if not self.scheduler_global_leader:
            self._scheduler_is_leader = True
            return True
        if not self._scheduler_leader_is_owner():
            return False
        renewed = await self.scheduler_leader.extend(self._instance_id, ttl_ms=self.scheduler_leader_ttl_ms)
        if not renewed:
            self._scheduler_is_leader = False
        return renewed

    def _scheduler_leader_is_owner(self) -> bool:
        return getattr(self, "_scheduler_is_leader", False)

    async def _release_scheduler_leader(self) -> None:
        if not self.scheduler_global_leader:
            self._scheduler_is_leader = False
            return
        if self._scheduler_leader_is_owner():
            await self.scheduler_leader.release(self._instance_id)
            self._scheduler_is_leader = False

    async def _schedule_once(self) -> bool:
        candidate = await self._select_next_candidate()
        if not candidate:
            return False
        queue_key, job_id, session_id, estimated_cost = candidate
        latest = await asyncio.to_thread(self.persistence.load_job, job_id)
        async with self._lock:
            current = self._jobs.get(job_id)
            if latest is not None:
                current = self._select_freshest_job(current, latest)
                self._jobs[job_id] = current
            if current is None or current.status != JobStatus.QUEUED:
                queue = self._queues.get(queue_key)
                if queue and queue[0] == job_id:
                    queue.popleft()
                self._active_key_set.discard(queue_key)
                if queue and queue_key not in self._active_key_set:
                    self._active_keys.append(queue_key)
                    self._active_key_set.add(queue_key)
                return True
            session_id = current.session_id
            estimated_cost = current.estimated_cost
        if not await self.session_locks.acquire(session_id, job_id, ttl_ms=self.session_lock_ttl_ms):
            async with self._lock:
                if queue_key in self._queues:
                    self._active_keys.append(queue_key)
                    self._active_key_set.add(queue_key)
            return False
        token = uuid.uuid4().hex
        try:
            should_release_lock = False
            async with self._lock:
                queue = self._queues.get(queue_key)
                job = self._jobs.get(job_id)
                if not queue or not job or not queue or queue[0] != job_id or job.status != JobStatus.QUEUED:
                    should_release_lock = True
                    if queue_key in self._queues:
                        self._active_keys.append(queue_key)
                        self._active_key_set.add(queue_key)
                else:
                    queue.popleft()
                    self._credits[queue_key] = max(self._credits[queue_key] - estimated_cost, 0)
                    self._running_sessions.add(session_id)
                    now = time.time()
                    self._transition_locked(job, JobStatus.ADMITTED)
                    job.attempt_count += 1
                    job.fencing_token += 1
                    job.execution_token = token
                    job.queued_at = job.queued_at or now
                    job.heartbeat_at = now
                    job.lease_expires_at = now + self.dispatch_lease_ttl_ms / 1000
                    self._emit_locked(job.id, "admitted", {"job_id": job.id, "worker_queue": True, "execution_token": token})
            if should_release_lock:
                with suppress(Exception):
                    await self.session_locks.release(session_id, job_id)
                return
            try:
                await self._run_persist_result(self.persistence.stage_dispatch, replace(job))
                await self.dispatch_backend.enqueue(job_id, queue_key=queue_key, execution_token=token)
                await self._run_persist_result(self.persistence.mark_dispatch_sent, job_id, job.fencing_token)
            except Exception as exc:
                dispatch_error = f"dispatch enqueue failed: {type(exc).__name__}: {exc}"[:2000]
                failed_dispatch = False
                async with self._lock:
                    queue = self._queues.get(queue_key)
                    job = self._jobs.get(job_id)
                    if queue is not None and job is not None:
                        self._running_sessions.discard(session_id)
                        job.execution_token = None
                        job.lease_expires_at = None
                        job.error_message = dispatch_error
                        if job.attempt_count >= self.max_attempts:
                            self._transition_locked(job, JobStatus.FAILED)
                            job.finished_at = time.time()
                            self._decrement_pending_locked(job)
                            self._persist_job(job)
                            self._emit_locked(job.id, "error", {"job_id": job.id, "error": dispatch_error})
                            failed_dispatch = True
                        else:
                            queue.appendleft(job_id)
                            if queue_key not in self._active_key_set:
                                self._active_keys.append(queue_key)
                                self._active_key_set.add(queue_key)
                            self._transition_locked(job, JobStatus.QUEUED)
                            self._persist_job(job)
                            self._emit_locked(
                                job.id,
                                "dispatch_retry",
                                {"job_id": job.id, "attempt_count": job.attempt_count, "error": dispatch_error},
                            )
                await self.session_locks.release(session_id, job_id)
                if failed_dispatch:
                    return False
                return False
            async with self._lock:
                queue = self._queues.get(queue_key)
                if queue:
                    self._active_keys.append(queue_key)
                    self._active_key_set.add(queue_key)
                else:
                    self._active_key_set.discard(queue_key)
            return True
        finally:
            pass

    async def _select_next_candidate(self) -> tuple[str, str, str, int] | None:
        async with self._lock:
            if not self._active_keys:
                return None
            turns = len(self._active_keys)
            for _ in range(turns):
                queue_key = self._active_keys.popleft()
                queue = self._queues.get(queue_key)
                if not queue:
                    self._active_key_set.discard(queue_key)
                    self._credits.pop(queue_key, None)
                    continue
                self._credits[queue_key] = min(self.max_credit, self._credits[queue_key] + self.base_quantum)
                job = self._jobs.get(queue[0])
                if not job:
                    queue.popleft()
                    self._active_keys.append(queue_key)
                    continue
                if self._credits[queue_key] < job.estimated_cost or job.session_id in self._running_sessions:
                    self._active_keys.append(queue_key)
                    continue
                self._active_key_set.discard(queue_key)
                return queue_key, job.id, job.session_id, job.estimated_cost
            return None

    async def _recover_stalled_jobs(self) -> None:
        stale_jobs = await asyncio.to_thread(self.persistence.load_stale_jobs, time.time())
        for job in stale_jobs:
            await self._recover_stalled_job(job)

    async def _recover_stalled_job(self, job: AgentJob) -> None:
        release_session = False
        async with self._lock:
            current = self._jobs.get(job.id) or job
            self._jobs[job.id] = current
            if current.status not in {JobStatus.ADMITTED, JobStatus.RUNNING}:
                return
            if current.lease_expires_at and current.lease_expires_at > time.time():
                return
            self._running_sessions.discard(current.session_id)
            current.execution_token = None
            current.heartbeat_at = None
            current.lease_expires_at = None
            if current.attempt_count >= self.max_attempts:
                current.status = JobStatus.FAILED
                current.finished_at = time.time()
                current.error_message = current.error_message or "job lease expired"
                self._decrement_pending_locked(current)
                self._persist_job(current)
                self._emit_locked(current.id, "error", {"job_id": current.id, "error": current.error_message})
                release_session = True
                return
            queue = self._queues[current.queue_key]
            if current.id not in queue:
                queue.appendleft(current.id)
            if current.queue_key not in self._active_key_set:
                self._active_keys.append(current.queue_key)
                self._active_key_set.add(current.queue_key)
            self._transition_locked(current, JobStatus.QUEUED)
            self._persist_job(current)
            self._emit_locked(current.id, "retry_queued", {"job_id": current.id, "attempt_count": current.attempt_count})
            release_session = True
        if release_session:
            with suppress(Exception):
                await self.session_locks.release(job.session_id, job.id)

    async def _restore_from_store(self) -> None:
        jobs = await asyncio.to_thread(self.persistence.load_jobs)
        events_by_job: dict[str, list[AgentEvent]] = {}
        for job in jobs:
            events_by_job[job.id] = await asyncio.to_thread(self.persistence.load_events_after, job.id, 0)
        async with self._lock:
            self._jobs.clear()
            self._events.clear()
            self._subscribers.clear()
            self._queues.clear()
            self._active_keys.clear()
            self._active_key_set.clear()
            self._credits.clear()
            self._running_sessions.clear()
            self._pending_total = 0
            self._pending_by_user.clear()
            for job in jobs:
                self._jobs[job.id] = job
                self._events[job.id] = events_by_job.get(job.id, [])
                if job.status == JobStatus.CANCEL_REQUESTED:
                    self._transition_locked(job, JobStatus.CANCELLED)
                    job.finished_at = job.finished_at or time.time()
                    job.execution_token = None
                    job.lease_expires_at = None
                    self._persist_job(job)
                    self._emit_locked(job.id, "cancelled", {"job_id": job.id, "reason": "recovered cancellation"})
                elif job.status in {JobStatus.QUEUED, JobStatus.ADMITTED, JobStatus.RUNNING}:
                    self._transition_locked(job, JobStatus.QUEUED)
                    self._pending_total += 1
                    self._pending_by_user[(job.tenant_id, job.user_id)] += 1
                    self._queues[job.queue_key].append(job.id)
                    if job.queue_key not in self._active_key_set:
                        self._active_keys.append(job.queue_key)
                        self._active_key_set.add(job.queue_key)

    async def _dispatch_loop(self) -> None:
        while True:
            try:
                message = await self.dispatch_backend.get(self._instance_id, block_ms=1000)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._dispatch_last_error = f"{type(exc).__name__}: {exc}"[:1000]
                await asyncio.sleep(0.25)
                continue
            if message is None:
                continue
            self._dispatch_last_error = None
            await self._dispatch_queue.put(message)

    async def _worker_loop(self, worker_id: str) -> None:
        while True:
            message = await self._dispatch_queue.get()
            self._dispatch_messages_received += 1
            should_ack = False
            try:
                await self._execute_job(message.job_id, worker_id, message.execution_token)
                should_ack = await self._settle_dispatch_message(message, worker_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._dispatch_worker_errors += 1
                reason = f"worker execution failed: {type(exc).__name__}: {exc}"[:2000]
                self._dispatch_last_worker_error = reason
                should_ack = await self._retry_dispatch_message(
                    message,
                    worker_id=worker_id,
                    reason=reason,
                    retry_running=True,
                )
            finally:
                if should_ack:
                    try:
                        await self.dispatch_backend.ack(message)
                        self._dispatch_messages_acked += 1
                    except Exception as exc:
                        self._dispatch_last_error = f"dispatch ack failed: {type(exc).__name__}: {exc}"[:1000]
                self._dispatch_queue.task_done()

    async def _settle_dispatch_message(self, message: DispatchMessage, worker_id: str) -> bool:
        """Confirm a broker message only after its execution has a durable disposition.

        A worker returning while the matching job is still ADMITTED is never a
        successful delivery. Requeue it immediately instead of ACKing and
        waiting for the lease sweeper to notice the loss.
        """
        async with self._lock:
            job = self._jobs.get(message.job_id)
            if job is None:
                # Dispatch is staged in SQL before XADD, so this is an orphaned
                # broker entry and is safe to discard.
                return True
            if message.execution_token and job.execution_token and message.execution_token != job.execution_token:
                return True
            if job.status in {
                JobStatus.RUNNING,
                JobStatus.SUCCEEDED,
                JobStatus.FAILED,
                JobStatus.CANCELLED,
                JobStatus.REJECTED,
            }:
                return True
            if job.status == JobStatus.QUEUED:
                return True

        reason = f"worker {worker_id} returned before the job entered running state"
        self._dispatch_last_worker_error = reason
        return await self._retry_dispatch_message(message, worker_id=worker_id, reason=reason)

    async def _retry_dispatch_message(
        self,
        message: DispatchMessage,
        *,
        worker_id: str,
        reason: str,
        retry_running: bool = False,
    ) -> bool:
        release_session: tuple[str, str] | None = None
        async with self._lock:
            job = self._jobs.get(message.job_id)
            if job is None:
                return True
            if message.execution_token and job.execution_token and message.execution_token != job.execution_token:
                return True
            if job.status in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.REJECTED}:
                return True
            if job.status == JobStatus.CANCEL_REQUESTED:
                self._mark_cancelled_locked(job)
                release_session = (job.session_id, job.id)
            elif job.status == JobStatus.QUEUED:
                return True
            elif job.status == JobStatus.RUNNING and not retry_running:
                # A duplicate delivery can observe a job owned by another
                # worker. Its message is stale and can be acknowledged.
                return True
            elif job.status in {JobStatus.ADMITTED, JobStatus.RUNNING}:
                self._running_sessions.discard(job.session_id)
                job.execution_token = None
                job.heartbeat_at = None
                job.lease_expires_at = None
                job.assigned_worker_id = None
                job.error_message = reason
                if job.attempt_count >= self.max_attempts:
                    self._transition_locked(job, JobStatus.FAILED)
                    job.finished_at = time.time()
                    self._decrement_pending_locked(job)
                    self._persist_job(job)
                    self._emit_locked(job.id, "error", {"job_id": job.id, "error": reason})
                else:
                    self._transition_locked(job, JobStatus.QUEUED)
                    queue = self._queues[job.queue_key]
                    if job.id not in queue:
                        queue.appendleft(job.id)
                    if job.queue_key not in self._active_key_set:
                        self._active_keys.append(job.queue_key)
                        self._active_key_set.add(job.queue_key)
                    self._persist_job(job)
                    self._emit_locked(
                        job.id,
                        "dispatch_retry",
                        {"job_id": job.id, "attempt_count": job.attempt_count, "error": reason},
                    )
                    self._dispatch_messages_retried += 1
                release_session = (job.session_id, job.id)
            else:
                return False

        if release_session is not None:
            with suppress(Exception):
                await self.session_locks.release(*release_session)
        return True

    async def _execute_job(self, job_id: str, worker_id: str, execution_token: str | None) -> None:
        heartbeat_task: asyncio.Task[Any] | None = None
        started_execution = False
        async with self._model_sem:
            try:
                async with self._lock:
                    job = self._jobs.get(job_id)
                if not job:
                    job = await asyncio.to_thread(self.persistence.load_job, job_id)
                    if job:
                        async with self._lock:
                            self._jobs[job_id] = job
                async with self._lock:
                    if not job:
                        return
                    if execution_token and job.execution_token and execution_token != job.execution_token:
                        return
                    if job.status in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.REJECTED}:
                        return
                    if job.status == JobStatus.RUNNING:
                        return
                    if job.status == JobStatus.CANCEL_REQUESTED:
                        started_execution = True
                        self._mark_cancelled_locked(job)
                        return
                    self._transition_locked(job, JobStatus.RUNNING)
                    job.started_at = time.time()
                    job.assigned_worker_id = worker_id
                    self._persist_job(job)
                    self._emit_locked(
                        job_id,
                        "running",
                        {"job_id": job_id, "worker_id": worker_id, "execution_token": execution_token},
                    )
                    started_execution = True
                    heartbeat_task = asyncio.create_task(
                        self._session_lock_heartbeat(job.session_id, job.id, job.id, execution_token),
                        name=f"session-lock-{job.id}",
                    )
                try:
                    if self.executor == "mock":
                        await self._execute_mock(job_id, execution_token)
                    else:
                        loop = asyncio.get_running_loop()
                        final_text, usage, tool_calls, final_metadata = await loop.run_in_executor(
                            self._proxy_executor,
                            self._execute_proxy_blocking,
                            job_id,
                            execution_token,
                        )
                        async with self._lock:
                            job = self._jobs.get(job_id)
                            if job and self._token_matches(job, execution_token):
                                if job.status == JobStatus.CANCEL_REQUESTED:
                                    self._mark_cancelled_locked(job)
                                elif final_metadata.get("output_contract_status") == "unmet":
                                    self._transition_locked(job, JobStatus.FAILED)
                                    job.finished_at = time.time()
                                    job.final_text = final_text
                                    job.final_metadata = final_metadata
                                    job.error_message = "required task contract was not satisfied"
                                    job.input_tokens = int(usage.get("input_tokens") or 0)
                                    job.output_tokens = int(usage.get("output_tokens") or 0)
                                    job.tool_call_count = tool_calls
                                    self._decrement_pending_locked(job)
                                    self._persist_job(job)
                                    self._emit_locked(
                                        job_id,
                                        "failed",
                                        {
                                            "job_id": job_id,
                                            "error": job.error_message,
                                            "requirements": final_metadata.get("requirements") or [],
                                        },
                                    )
                                else:
                                    self._transition_locked(job, JobStatus.SUCCEEDED)
                                    job.finished_at = time.time()
                                    job.final_text = final_text
                                    job.final_metadata = final_metadata
                                    job.input_tokens = int(usage.get("input_tokens") or 0)
                                    job.output_tokens = int(usage.get("output_tokens") or 0)
                                    job.tool_call_count = tool_calls
                                    self._decrement_pending_locked(job)
                                    self._persist_job(job)
                                    self._emit_locked(
                                        job_id,
                                        "final",
                                        {
                                            "job_id": job_id,
                                            "text": final_text,
                                            "usage": usage,
                                            **final_metadata,
                                        },
                                    )
                except Exception as exc:
                    async with self._lock:
                        job = self._jobs.get(job_id)
                        if job and self._token_matches(job, execution_token):
                            if job.status == JobStatus.CANCEL_REQUESTED:
                                self._mark_cancelled_locked(job)
                            else:
                                self._transition_locked(job, JobStatus.FAILED)
                                job.finished_at = time.time()
                                job.error_message = str(exc)[:2000]
                                self._decrement_pending_locked(job)
                                self._persist_job(job)
                                self._emit_locked(job_id, "error", {"job_id": job_id, "error": job.error_message})
            finally:
                if heartbeat_task is not None:
                    heartbeat_task.cancel()
                    with suppress(asyncio.CancelledError, Exception):
                        await heartbeat_task
                if started_execution:
                    with suppress(Exception):
                        job = self._jobs.get(job_id)
                        if job:
                            await self.session_locks.release(job.session_id, job.id)
                if started_execution:
                    async with self._lock:
                        job = self._jobs.get(job_id)
                        if job:
                            self._running_sessions.discard(job.session_id)

    async def _execute_mock(self, job_id: str, execution_token: str | None) -> None:
        job = await self._get_job_for_execution(job_id)
        if not job:
            return
        if not self._token_matches(job, execution_token):
            return
        prompt = job.prompt
        await asyncio.sleep(0.01)
        if not self._token_matches(await self._get_job_for_execution(job_id), execution_token):
            return
        self._emit_sync(job_id, "model_request", {"job_id": job_id, "mode": "mock"})
        await asyncio.sleep(0.02)
        if not self._token_matches(await self._get_job_for_execution(job_id), execution_token):
            return
        await self._emit(job_id, "text_delta", {"text": f"已接收问题：{prompt[:80]}"})
        await asyncio.sleep(0.01)
        final_text = f"高并发测试模拟回答：{prompt}"
        async with self._lock:
            job = self._jobs.get(job_id)
            if not job or not self._token_matches(job, execution_token):
                return
            if job.status == JobStatus.CANCEL_REQUESTED:
                self._mark_cancelled_locked(job)
                return
            self._transition_locked(job, JobStatus.SUCCEEDED)
            job.finished_at = time.time()
            job.final_text = final_text
            job.input_tokens = len(prompt)
            job.output_tokens = len(final_text)
            self._decrement_pending_locked(job)
            self._persist_job(job)
            self._emit_locked(job_id, "final", {"job_id": job_id, "text": final_text})

    def _execute_proxy_blocking(self, job_id: str, execution_token: str | None) -> tuple[str, dict[str, Any], int, dict[str, Any]]:
        job = self._jobs.get(job_id)
        if not job:
            job = self.persistence.load_job(job_id)
            if not job:
                return "", {}, 0, {}
        if not self._token_matches(job, execution_token):
            return "", {}, 0, {}
        principal = Principal(
            tenant_id=job.tenant_id,
            user_id=job.user_id,
            role_ids=job.role_ids_snapshot,
            data_scope=job.data_scope_snapshot,
            allowed_tools=job.allowed_tools_snapshot,
            auth_method="job-snapshot",
        )
        runtime_token = issue_runtime_token(
            job_id=job.id,
            session_id=job.session_id,
            principal=principal,
            trace_id=job.trace_id,
        )
        response = requests.post(
            f"{self.agent_base_url}/api/chat_stream",
            json={"prompt": job.prompt, "session_id": job.session_id, "max_turns": job.max_turns},
            headers={"Authorization": f"Bearer {runtime_token}", "X-Agent-Job-ID": job.id},
            stream=True,
            timeout=(10, self.upstream_read_timeout_seconds),
        )
        response.raise_for_status()
        with self._active_proxy_lock:
            self._active_proxy_responses[job_id] = response
        final_text = ""
        usage: dict[str, Any] = {}
        tool_calls = 0
        final_metadata: dict[str, Any] = {}
        try:
            for raw_line in response.iter_lines(decode_unicode=True):
                if not raw_line:
                    continue
                current_job = self._jobs.get(job_id)
                if current_job and (
                    not self._token_matches(current_job, execution_token)
                    or current_job.status == JobStatus.CANCEL_REQUESTED
                ):
                    break
                payload = json.loads(raw_line)
                event_type = str(payload.get("type") or "message")
                if event_type == "tool_use":
                    tool_calls += 1
                if event_type == "final":
                    final_text = str(payload.get("text") or "")
                    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
                    final_metadata = {
                        "citations": payload.get("citations") if isinstance(payload.get("citations"), list) else [],
                        "claims": payload.get("claims") if isinstance(payload.get("claims"), list) else [],
                        "evidence_status": str(payload.get("evidence_status") or "not_applicable"),
                        "route": str(payload.get("route") or "general"),
                        "route_policy_version": str(payload.get("route_policy_version") or "unknown"),
                        "output_contract_status": str(payload.get("output_contract_status") or "not_required"),
                        "task_contract_status": str(payload.get("task_contract_status") or payload.get("output_contract_status") or "not_required"),
                        "requirements": payload.get("requirements") if isinstance(payload.get("requirements"), list) else [],
                        "termination_reason": str(payload.get("termination_reason") or "unknown"),
                        "run_state": payload.get("run_state") if isinstance(payload.get("run_state"), dict) else {},
                        "tool_scheduler_ledger": payload.get("tool_scheduler_ledger") if isinstance(payload.get("tool_scheduler_ledger"), dict) else {},
                        "run_budget": payload.get("run_budget") if isinstance(payload.get("run_budget"), dict) else {},
                        "route_decision": payload.get("route_decision") if isinstance(payload.get("route_decision"), dict) else {},
                        "model_tier": str(payload.get("model_tier") or ""),
                        "budget_class": str(payload.get("budget_class") or ""),
                        "model_routing": payload.get("model_routing") if isinstance(payload.get("model_routing"), dict) else {},
                        "tool_audit": payload.get("tool_audit") if isinstance(payload.get("tool_audit"), list) else [],
                    }
                # Runtime's final frame is a candidate completion. The worker must
                # validate the task contract before publishing a terminal event;
                # otherwise the UI can show success while the persisted Job fails.
                if event_type != "final":
                    self._emit_from_proxy_thread(job_id, event_type, payload)
                # Keep the upstream response open through its short telemetry tail.
                # Releasing the backend model slot at the first final frame races the
                # upstream semaphore release and causes avoidable 429 bursts.
                if event_type in {"error", "cancelled"}:
                    break
        finally:
            with self._active_proxy_lock:
                self._active_proxy_responses.pop(job_id, None)
            response.close()
        current_job = self._jobs.get(job_id)
        if current_job and current_job.status == JobStatus.CANCEL_REQUESTED:
            return "", {}, tool_calls, {}
        if not final_text:
            raise RuntimeError("Agent stream ended without a final event")
        return final_text, usage, tool_calls, final_metadata

    def _emit_from_proxy_thread(self, job_id: str, event_type: str, payload: dict[str, Any]) -> None:
        if self._loop is None:
            return
        future = asyncio.run_coroutine_threadsafe(self._emit(job_id, event_type, payload), self._loop)
        future.result(timeout=5)

    def _close_active_proxy_response(self, job_id: str) -> None:
        with self._active_proxy_lock:
            response = self._active_proxy_responses.get(job_id)
        if response is not None:
            with suppress(Exception):
                response.close()

    async def _get_job_for_execution(self, job_id: str) -> AgentJob | None:
        async with self._lock:
            job = self._jobs.get(job_id)
        if job:
            return job
        job = await asyncio.to_thread(self.persistence.load_job, job_id)
        if not job:
            return None
        async with self._lock:
            self._jobs[job_id] = job
        return job

    def _token_matches(self, job: AgentJob, execution_token: str | None) -> bool:
        if execution_token is None:
            return True
        return job.execution_token == execution_token

    async def _session_lock_heartbeat(self, session_id: str, owner_id: str, job_id: str, execution_token: str | None) -> None:
        try:
            while True:
                await asyncio.sleep(max(self.session_lock_heartbeat_ms, 1000) / 1000)
                async with self._lock:
                    job = self._jobs.get(job_id)
                    if not job or not self._token_matches(job, execution_token):
                        return
                    job.heartbeat_at = time.time()
                    job.lease_expires_at = job.heartbeat_at + self.dispatch_lease_ttl_ms / 1000
                    self._persist_job(job)
                ok = await self.session_locks.extend(session_id, owner_id, ttl_ms=self.session_lock_ttl_ms)
                if not ok:
                    return
        except asyncio.CancelledError:
            raise

    async def _emit(self, job_id: str, event_type: str, payload: dict[str, Any]) -> None:
        async with self._lock:
            self._emit_locked(job_id, event_type, payload)

    def _emit_sync(self, job_id: str, event_type: str, payload: dict[str, Any]) -> None:
        events = self._events[job_id]
        event = AgentEvent(seq=len(events) + 1, event_type=event_type, payload=payload)
        events.append(event)
        self._persist_event(job_id, event)
        for subscriber in list(self._subscribers.get(job_id, set())):
            try:
                subscriber.put_nowait(event)
            except asyncio.QueueFull:
                pass

    def _emit_locked(self, job_id: str, event_type: str, payload: dict[str, Any]) -> None:
        events = self._events[job_id]
        event = AgentEvent(seq=len(events) + 1, event_type=event_type, payload=payload)
        events.append(event)
        self._persist_event(job_id, event)
        for subscriber in list(self._subscribers.get(job_id, set())):
            try:
                subscriber.put_nowait(event)
            except asyncio.QueueFull:
                pass

    def _mark_cancelled_locked(self, job: AgentJob) -> None:
        self._transition_locked(job, JobStatus.CANCELLED)
        job.finished_at = time.time()
        self._running_sessions.discard(job.session_id)
        self._decrement_pending_locked(job)
        self._persist_job(job)
        self._emit_locked(job.id, "cancelled", {"job_id": job.id})

    def _transition_locked(self, job: AgentJob, target: JobStatus) -> None:
        ensure_job_transition(job.status, target)
        job.status = target

    def _decrement_pending_locked(self, job: AgentJob) -> None:
        if self._pending_total > 0:
            self._pending_total -= 1
        user_key = (job.tenant_id, job.user_id)
        if self._pending_by_user.get(user_key, 0) > 0:
            self._pending_by_user[user_key] -= 1
            if self._pending_by_user[user_key] <= 0:
                self._pending_by_user.pop(user_key, None)

    def _persist_job(self, job: AgentJob) -> None:
        if job.id in self._pending_job_persistence_ids:
            return
        try:
            self._pending_job_persistence_ids.add(job.id)
            self._persistence_queue.put_nowait(("job", job.id))
        except asyncio.QueueFull:
            self._pending_job_persistence_ids.discard(job.id)

    def _persist_event(self, job_id: str, event: AgentEvent) -> None:
        # Token-sized deltas are transient delivery data. Persisting each one as
        # an SQL row creates enough write amplification to delay leases and finals.
        if event.event_type == "text_delta":
            return
        try:
            self._persistence_queue.put_nowait(("event", (job_id, event)))
        except asyncio.QueueFull:
            pass

    async def _persistence_loop(self) -> None:
        while True:
            kind, payload = await self._persistence_queue.get()
            try:
                if kind == "job":
                    job_id = str(payload)
                    async with self._lock:
                        current = self._jobs.get(job_id)
                        snapshot = replace(current) if current is not None else None
                        self._pending_job_persistence_ids.discard(job_id)
                    if snapshot is not None:
                        await self._run_persist(self.persistence.save_job, snapshot)
                elif kind == "event":
                    job_id, event = payload
                    await self._run_persist(self.persistence.save_event, job_id, event)
            finally:
                self._persistence_queue.task_done()

    async def _run_persist(self, fn: Any, *args: Any) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(self._persistence_executor, lambda: fn(*args))

    async def _run_persist_result(self, fn: Any, *args: Any) -> Any:
        if type(self.persistence).__name__ == "MemoryAgentJobPersistence":
            return fn(*args)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._persistence_executor, lambda: fn(*args))

    def _estimate_cost(self, prompt: str) -> int:
        return estimate_agent_job_route(prompt).estimated_cost


_AGENT_JOB_SERVICE: AgentJobService | None = None


def get_agent_job_service() -> AgentJobService:
    global _AGENT_JOB_SERVICE
    if _AGENT_JOB_SERVICE is None:
        _AGENT_JOB_SERVICE = AgentJobService()
    return _AGENT_JOB_SERVICE
