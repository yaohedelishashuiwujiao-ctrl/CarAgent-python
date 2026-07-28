from __future__ import annotations

import concurrent.futures
import os
import socket
import threading
import time
import urllib.error
from dataclasses import replace
from typing import Any, Callable

from .context import ToolContext
from .errors import ToolInputError, ToolPermissionError
from .protocol import ToolCall, ToolOutcomeStatus, ToolResult
from .registry_types import ToolExecutionPolicyView


class _Pool:
    def __init__(self, name: str, workers: int, queue_capacity: int) -> None:
        self.name = name
        self.executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=workers,
            thread_name_prefix=f"agent-tool-{name}",
        )
        self.capacity = threading.BoundedSemaphore(workers + queue_capacity)

    def submit(self, fn: Callable[[], ToolResult]) -> concurrent.futures.Future[ToolResult] | None:
        if not self.capacity.acquire(blocking=False):
            return None
        try:
            future = self.executor.submit(fn)
        except Exception:
            self.capacity.release()
            raise
        future.add_done_callback(lambda _future: self.capacity.release())
        return future


class ToolExecutionCoordinator:
    """Process-local bounded execution pools shared by all Runtime sessions."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pools: dict[str, _Pool] = {}

    def _pool(self, name: str) -> _Pool:
        normalized = (name or "tool").strip().lower()
        with self._lock:
            pool = self._pools.get(normalized)
            if pool is not None:
                return pool
            default_workers = {
                "sql": 8,
                "web": 8,
                "knowledge": 8,
                "artifact": 2,
                "tool": 16,
            }.get(normalized, 8)
            env_key = f"CLAWD_TOOL_POOL_{normalized.upper()}_WORKERS"
            workers = max(1, min(int(os.getenv(env_key, str(default_workers))), 64))
            queue_capacity = max(0, min(int(os.getenv("CLAWD_TOOL_POOL_QUEUE_CAPACITY", str(workers))), 256))
            pool = _Pool(normalized, workers, queue_capacity)
            self._pools[normalized] = pool
            return pool

    def execute(
        self,
        fn: Callable[[], ToolResult],
        *,
        pool_name: str,
        timeout_s: float | None,
        enforce_timeout: bool,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> tuple[ToolResult | None, str | None]:
        if is_cancelled is not None and is_cancelled():
            return None, "cancelled"
        pool = self._pool(pool_name)
        future = pool.submit(fn)
        if future is None:
            return None, "pool_saturated"
        started = time.monotonic()
        while True:
            if is_cancelled is not None and is_cancelled():
                future.cancel()
                return None, "cancelled"
            wait_s = 0.1
            if enforce_timeout and timeout_s is not None:
                remaining = max(0.01, float(timeout_s)) - (time.monotonic() - started)
                if remaining <= 0:
                    future.cancel()
                    return None, "timeout"
                wait_s = min(wait_s, remaining)
            try:
                return future.result(timeout=wait_s), None
            except concurrent.futures.TimeoutError:
                continue


COORDINATOR = ToolExecutionCoordinator()


def execute_tool_with_policy(
    tool: Any,
    spec: Any,
    call: ToolCall,
    context: ToolContext,
) -> ToolResult:
    policy = ToolExecutionPolicyView.from_spec(spec)
    max_attempts = policy.max_attempts if policy.idempotent else 1
    started = time.monotonic()
    last_result: ToolResult | None = None

    for attempt in range(1, max_attempts + 1):
        attempt_started = time.monotonic()
        enforce_timeout = policy.idempotent and policy.side_effect == "none"

        def invoke() -> ToolResult:
            try:
                result = tool.run(call.input, context)
                if not isinstance(result, ToolResult):
                    return ToolResult(name=spec.name, output=result)
                return result
            except Exception as exc:  # normalized here so retry policy is deterministic
                return _exception_result(spec.name, exc)

        result, coordinator_error = COORDINATOR.execute(
            invoke,
            pool_name=policy.concurrency_pool,
            timeout_s=policy.timeout_s,
            enforce_timeout=enforce_timeout,
            is_cancelled=context.is_cancelled,
        )
        if coordinator_error == "pool_saturated":
            result = ToolResult(
                name=spec.name,
                output={"error": f"tool resource pool '{policy.concurrency_pool}' is saturated"},
                is_error=True,
                outcome_status=ToolOutcomeStatus.TRANSIENT_FAILURE,
                reason_code="RESOURCE_POOL_SATURATED",
                retryable=True,
            )
        elif coordinator_error == "timeout":
            result = ToolResult(
                name=spec.name,
                output={"error": f"tool execution exceeded {policy.timeout_s}s"},
                is_error=True,
                outcome_status=ToolOutcomeStatus.TIMEOUT,
                reason_code="TOOL_EXECUTION_TIMEOUT",
                # The underlying call may still be finishing; retrying could
                # duplicate work even for nominally idempotent integrations.
                retryable=False,
            )
        elif coordinator_error == "cancelled":
            result = ToolResult(
                name=spec.name,
                output={"error": "tool execution cancelled"},
                is_error=True,
                outcome_status=ToolOutcomeStatus.CANCELLED,
                reason_code="TOOL_EXECUTION_CANCELLED",
                retryable=False,
            )
        assert result is not None
        last_result = result
        should_retry = (
            attempt < max_attempts
            and policy.idempotent
            and result.retryable
            and result.outcome_status.value in policy.retryable_outcomes
        )
        context.audit_events.append(
            {
                "event": "tool_execution_attempt",
                "job_id": context.job_id,
                "trace_id": context.trace_id,
                "tool_name": spec.name,
                "attempt": attempt,
                "max_attempts": max_attempts,
                "resource_pool": policy.concurrency_pool,
                "duration_ms": round((time.monotonic() - attempt_started) * 1000, 3),
                "outcome_status": result.outcome_status.value,
                "reason_code": result.reason_code,
                "retry_scheduled": should_retry,
                "timeout_enforcement": "runtime" if enforce_timeout else "tool_cooperative",
            }
        )
        if not should_retry:
            break
        base_delay = max(0.0, float(os.getenv("CLAWD_TOOL_RETRY_BASE_DELAY_SECONDS", "0.2")))
        time.sleep(min(base_delay * (2 ** (attempt - 1)), 2.0))

    assert last_result is not None
    diagnostics = {
        **dict(last_result.diagnostics),
        "attempt_count": attempt,
        "execution_duration_ms": round((time.monotonic() - started) * 1000, 3),
        "resource_pool": policy.concurrency_pool,
    }
    return replace(last_result, diagnostics=diagnostics)


def _exception_result(tool_name: str, exc: Exception) -> ToolResult:
    if isinstance(exc, ToolInputError):
        status = ToolOutcomeStatus.INVALID_INPUT
        reason = "TOOL_INPUT_ERROR"
        retryable = False
    elif isinstance(exc, ToolPermissionError):
        status = ToolOutcomeStatus.PERMISSION_DENIED
        reason = "TOOL_PERMISSION_ERROR"
        retryable = False
    elif isinstance(exc, (TimeoutError, socket.timeout, ConnectionError, urllib.error.URLError)):
        status = ToolOutcomeStatus.TRANSIENT_FAILURE
        reason = "TOOL_DEPENDENCY_TRANSIENT_FAILURE"
        retryable = True
    else:
        status = ToolOutcomeStatus.PERMANENT_FAILURE
        reason = "TOOL_EXECUTION_EXCEPTION"
        retryable = False
    return ToolResult(
        name=tool_name,
        output={"error": str(exc)[:2000]},
        is_error=True,
        outcome_status=status,
        reason_code=reason,
        retryable=retryable,
        diagnostics={"exception_type": type(exc).__name__},
    )
