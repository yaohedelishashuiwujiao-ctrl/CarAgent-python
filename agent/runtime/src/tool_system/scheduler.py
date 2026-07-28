from __future__ import annotations

import concurrent.futures
import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any

from .context import ToolContext
from .preflight import EligibilityStatus
from .protocol import ToolCall, ToolOutcomeStatus, ToolResult
from .registry import ToolRegistry, preflight_outcome_status


@dataclass(frozen=True)
class ScheduledToolResult:
    call: ToolCall
    result: ToolResult
    dispatched: bool


class ToolCallScheduler:
    """Preflight, deduplicate, and batch ready tool calls before dispatch.

    This sits above ToolRegistry.dispatch. Registry still owns schema,
    permission, and execution policy enforcement; the scheduler reduces
    predictable failures before they occupy tool resource pools.
    """

    def __init__(self, registry: ToolRegistry, context: ToolContext) -> None:
        self.registry = registry
        self.context = context

    def execute(
        self,
        calls: list[ToolCall],
        *,
        mode: str,
        allow_parallel: bool = True,
        max_workers: int = 4,
        dedupe_scope: str = "batch",
    ) -> list[ScheduledToolResult]:
        if not calls:
            return []
        prepared = self._prepare(calls, mode=mode, dedupe_scope=dedupe_scope)
        dispatchable = [item for item in prepared if item.result is None]
        results_by_id: dict[str, ToolResult] = {
            item.key: item.result
            for item in prepared
            if item.result is not None
        }
        batch = self._can_batch([item.call for item in dispatchable])
        parallel = (not batch) and self._can_parallelize([item.call for item in dispatchable]) if allow_parallel else False
        self.context.audit_events.append(
            {
                "event": "tool_scheduler_decision",
                "job_id": self.context.job_id,
                "trace_id": self.context.trace_id,
                "mode": mode,
                "requested_count": len(calls),
                "dispatchable_count": len(dispatchable),
                "rejected_count": len(prepared) - len(dispatchable),
                "batch": batch,
                "parallel": parallel,
                "tool_names": [item.call.name for item in dispatchable],
            }
        )
        if dispatchable and batch:
            self._dispatch_batch(dispatchable, results_by_id, mode=mode)
        elif dispatchable and parallel:
            self._dispatch_parallel(dispatchable, results_by_id, mode=mode, max_workers=max_workers)
        else:
            for item in dispatchable:
                results_by_id[item.key] = self._dispatch_one(item.call)
        ordered: list[ScheduledToolResult] = []
        for item in prepared:
            result = results_by_id[item.key]
            ordered.append(ScheduledToolResult(call=item.call, result=result, dispatched=item.result is None))
        self._record_ledger(ordered, mode=mode)
        return ordered

    def _prepare(self, calls: list[ToolCall], *, mode: str, dedupe_scope: str) -> list["_PreparedCall"]:
        prepared: list[_PreparedCall] = []
        seen: set[str] = set()
        ledger = self._ledger()
        seen_in_run = ledger.setdefault("call_fingerprints", [])
        for index, raw_call in enumerate(calls):
            call, _aliases = self.registry.normalize_call(raw_call)
            key = call.tool_use_id or f"scheduled_{index + 1}"
            fingerprint = _fingerprint(call)
            if fingerprint in seen:
                prepared.append(
                    _PreparedCall(
                        key=key,
                        call=call,
                        result=ToolResult(
                            name=call.name,
                            output={
                                "error": "duplicate tool call in the same scheduler batch",
                                "reason_code": "DUPLICATE_TOOL_CALL_IN_BATCH",
                            },
                            is_error=True,
                            tool_use_id=call.tool_use_id,
                            outcome_status=ToolOutcomeStatus.INVALID_INPUT,
                            reason_code="DUPLICATE_TOOL_CALL_IN_BATCH",
                        ),
                    )
                )
                self._audit_rejection(call, mode=mode, reason_code="DUPLICATE_TOOL_CALL_IN_BATCH")
                continue
            if dedupe_scope == "run" and fingerprint in seen_in_run:
                prepared.append(
                    _PreparedCall(
                        key=key,
                        call=call,
                        result=ToolResult(
                            name=call.name,
                            output={
                                "error": "equivalent tool call already ran in this task",
                                "reason_code": "DUPLICATE_TOOL_CALL_IN_RUN",
                            },
                            is_error=True,
                            tool_use_id=call.tool_use_id,
                            outcome_status=ToolOutcomeStatus.INVALID_INPUT,
                            reason_code="DUPLICATE_TOOL_CALL_IN_RUN",
                        ),
                    )
                )
                self._audit_rejection(call, mode=mode, reason_code="DUPLICATE_TOOL_CALL_IN_RUN")
                continue
            seen.add(fingerprint)
            if dedupe_scope == "run":
                seen_in_run.append(fingerprint)
            preflight = self.registry.preflight(call, self.context)
            if preflight.status == EligibilityStatus.ELIGIBLE:
                prepared.append(_PreparedCall(key=key, call=call, result=None))
                continue
            result = ToolResult(
                name=call.name,
                output={
                    "error": preflight.message,
                    "preflight_rejected": True,
                    "reason_code": preflight.reason_code,
                    "alternative_capabilities": list(preflight.alternative_capabilities),
                },
                is_error=True,
                tool_use_id=call.tool_use_id,
                outcome_status=(
                    ToolOutcomeStatus.APPROVAL_REQUIRED
                    if preflight.status == EligibilityStatus.NEEDS_APPROVAL
                    else preflight_outcome_status(preflight)
                ),
                reason_code=preflight.reason_code,
                retryable=preflight.retryable,
                diagnostics=preflight.diagnostics,
            )
            prepared.append(_PreparedCall(key=key, call=call, result=result))
            self._audit_rejection(call, mode=mode, reason_code=preflight.reason_code)
        return prepared

    def _can_parallelize(self, calls: list[ToolCall]) -> bool:
        if len(calls) <= 1:
            return False
        for call in calls:
            tool = self.registry.get(call.name)
            if tool is None:
                return False
            spec = tool.spec()
            if not (
                spec.is_read_only
                and spec.execution.supports_parallel
                and spec.execution.side_effect == "none"
                and spec.execution.idempotent
            ):
                return False
        return True

    def _can_batch(self, calls: list[ToolCall]) -> bool:
        if len(calls) <= 1:
            return False
        first = self.registry.get(calls[0].name)
        if first is None:
            return False
        spec = first.spec()
        if not (
            spec.is_read_only
            and spec.execution.supports_batch
            and spec.execution.side_effect == "none"
            and spec.execution.idempotent
        ):
            return False
        return all(call.name.lower() == spec.name.lower() for call in calls)

    def _dispatch_batch(
        self,
        items: list["_PreparedCall"],
        results_by_id: dict[str, ToolResult],
        *,
        mode: str,
    ) -> None:
        started = time.monotonic()
        self.context.audit_events.append(
            {
                "event": "batch_tool_group_started",
                "job_id": self.context.job_id,
                "trace_id": self.context.trace_id,
                "tool_name": items[0].call.name if items else "",
                "batch_size": len(items),
                "mode": mode,
            }
        )
        results = self.registry.dispatch_batch([item.call for item in items], self.context)
        for item, result in zip(items, results):
            results_by_id[item.key] = result
        self.context.audit_events.append(
            {
                "event": "batch_tool_group_completed",
                "job_id": self.context.job_id,
                "trace_id": self.context.trace_id,
                "tool_name": items[0].call.name if items else "",
                "batch_size": len(items),
                "elapsed_ms": round((time.monotonic() - started) * 1000),
                "mode": mode,
            }
        )

    def _dispatch_parallel(
        self,
        items: list["_PreparedCall"],
        results_by_id: dict[str, ToolResult],
        *,
        mode: str,
        max_workers: int,
    ) -> None:
        started = time.monotonic()
        workers = max(1, min(max_workers, len(items)))
        self.context.audit_events.append(
            {
                "event": "parallel_tool_batch_started",
                "job_id": self.context.job_id,
                "trace_id": self.context.trace_id,
                "tool_names": [item.call.name for item in items],
                "batch_size": len(items),
                "max_workers": workers,
                "mode": mode,
            }
        )
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers, thread_name_prefix="tool-scheduler") as executor:
            future_to_key = {
                executor.submit(self._dispatch_one, item.call): item.key
                for item in items
            }
            for future in concurrent.futures.as_completed(future_to_key):
                key = future_to_key[future]
                try:
                    results_by_id[key] = future.result()
                except Exception as exc:
                    item = next(candidate for candidate in items if candidate.key == key)
                    results_by_id[key] = ToolResult(
                        name=item.call.name,
                        output={"error": str(exc)[:2000]},
                        is_error=True,
                        tool_use_id=item.call.tool_use_id,
                        outcome_status=ToolOutcomeStatus.PERMANENT_FAILURE,
                        reason_code="SCHEDULER_DISPATCH_EXCEPTION",
                    )
        self.context.audit_events.append(
            {
                "event": "parallel_tool_batch_completed",
                "job_id": self.context.job_id,
                "trace_id": self.context.trace_id,
                "tool_names": [item.call.name for item in items],
                "batch_size": len(items),
                "elapsed_ms": round((time.monotonic() - started) * 1000),
                "mode": mode,
            }
        )

    def _dispatch_one(self, call: ToolCall) -> ToolResult:
        return self.registry.dispatch(call, self.context)

    def _record_ledger(self, results: list[ScheduledToolResult], *, mode: str) -> None:
        ledger = self._ledger()
        ledger["requested"] = int(ledger.get("requested") or 0) + len(results)
        dispatched = sum(1 for item in results if item.dispatched)
        rejected = len(results) - dispatched
        ledger["dispatched"] = int(ledger.get("dispatched") or 0) + dispatched
        ledger["rejected"] = int(ledger.get("rejected") or 0) + rejected
        status_counts = ledger.setdefault("status_counts", {})
        reason_counts = ledger.setdefault("reason_counts", {})
        batch_statuses: list[dict[str, Any]] = []
        for item in results:
            status = item.result.outcome_status.value
            reason = item.result.reason_code or ""
            status_counts[status] = int(status_counts.get(status) or 0) + 1
            if reason:
                reason_counts[reason] = int(reason_counts.get(reason) or 0) + 1
            batch_statuses.append(
                {
                    "tool_name": item.call.name,
                    "outcome_status": status,
                    "reason_code": reason or None,
                    "dispatched": item.dispatched,
                }
            )
        history = ledger.setdefault("batches", [])
        history.append({"mode": mode, "results": batch_statuses})
        if len(history) > 20:
            del history[:-20]
        self.context.audit_events.append(
            {
                "event": "tool_scheduler_ledger_updated",
                "job_id": self.context.job_id,
                "trace_id": self.context.trace_id,
                "mode": mode,
                "requested_total": ledger["requested"],
                "dispatched_total": ledger["dispatched"],
                "rejected_total": ledger["rejected"],
                "status_counts": dict(status_counts),
                "reason_counts": dict(reason_counts),
            }
        )

    def _ledger(self) -> dict[str, Any]:
        ledger = self.context.runtime_state.setdefault("tool_scheduler_ledger", {})
        if not isinstance(ledger, dict):
            ledger = {}
            self.context.runtime_state["tool_scheduler_ledger"] = ledger
        return ledger

    def _audit_rejection(self, call: ToolCall, *, mode: str, reason_code: str) -> None:
        self.context.audit_events.append(
            {
                "event": "tool_scheduler_rejected",
                "job_id": self.context.job_id,
                "trace_id": self.context.trace_id,
                "mode": mode,
                "tool_name": call.name,
                "reason_code": reason_code,
            }
        )


@dataclass(frozen=True)
class _PreparedCall:
    key: str
    call: ToolCall
    result: ToolResult | None


def _fingerprint(call: ToolCall) -> str:
    payload = {"name": call.name.lower(), "input": call.input}
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
