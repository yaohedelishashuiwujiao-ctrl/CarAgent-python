from __future__ import annotations

import time
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class JobStatus(str, Enum):
    QUEUED = "queued"
    ADMITTED = "admitted"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


TERMINAL_JOB_STATUSES = frozenset(
    {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.REJECTED}
)

ALLOWED_JOB_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.QUEUED: frozenset({JobStatus.ADMITTED, JobStatus.CANCELLED, JobStatus.REJECTED}),
    JobStatus.ADMITTED: frozenset({JobStatus.RUNNING, JobStatus.QUEUED, JobStatus.CANCEL_REQUESTED, JobStatus.CANCELLED, JobStatus.FAILED}),
    JobStatus.RUNNING: frozenset({JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCEL_REQUESTED, JobStatus.CANCELLED, JobStatus.QUEUED}),
    JobStatus.CANCEL_REQUESTED: frozenset({JobStatus.CANCELLED, JobStatus.FAILED}),
    JobStatus.SUCCEEDED: frozenset(),
    JobStatus.FAILED: frozenset(),
    JobStatus.CANCELLED: frozenset(),
    JobStatus.REJECTED: frozenset(),
}


def ensure_job_transition(current: JobStatus, target: JobStatus) -> None:
    if current == target:
        return
    if target not in ALLOWED_JOB_TRANSITIONS[current]:
        raise ValueError(f"illegal agent job transition: {current.value} -> {target.value}")


@dataclass
class AgentJob:
    id: str
    tenant_id: str
    user_id: str
    session_id: str
    prompt: str
    queue_key: str
    estimated_cost: int
    max_turns: int
    trace_id: str | None = None
    idempotency_key: str | None = None
    role_ids_snapshot: tuple[str, ...] = ()
    data_scope_snapshot: dict[str, Any] = field(default_factory=dict)
    allowed_tools_snapshot: tuple[str, ...] = ()
    auth_context_version: int = 1
    status: JobStatus = JobStatus.QUEUED
    priority: int = 0
    attempt_count: int = 0
    execution_token: str | None = None
    fencing_token: int = 0
    created_at: float = field(default_factory=time.time)
    queued_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    heartbeat_at: float | None = None
    lease_expires_at: float | None = None
    assigned_worker_id: str | None = None
    error_message: str | None = None
    final_text: str | None = None
    final_metadata: dict[str, Any] = field(default_factory=dict)
    input_tokens: int = 0
    output_tokens: int = 0
    tool_call_count: int = 0

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "AgentJob":
        return cls(
            id=str(row["id"]),
            tenant_id=str(row["tenant_id"]),
            user_id=str(row["user_id"]),
            session_id=str(row["session_id"]),
            prompt=str(row["prompt"]),
            queue_key=str(row["queue_key"]),
            estimated_cost=int(row["estimated_cost"]),
            max_turns=int(row["max_turns"]),
            trace_id=row.get("trace_id"),
            idempotency_key=row.get("idempotency_key"),
            role_ids_snapshot=_string_tuple(row.get("role_ids_json")),
            data_scope_snapshot=_json_object(row.get("data_scope_json")),
            allowed_tools_snapshot=_string_tuple(row.get("allowed_tools_json")),
            auth_context_version=int(row.get("auth_context_version") or 1),
            status=JobStatus(str(row["status"])),
            priority=int(row.get("priority") or 0),
            attempt_count=int(row.get("attempt_count") or 0),
            execution_token=row.get("execution_token"),
            fencing_token=int(row.get("fencing_token") or 0),
            created_at=float(row["created_at"]),
            queued_at=float(row["queued_at"]),
            started_at=float(row["started_at"]) if row.get("started_at") is not None else None,
            finished_at=float(row["finished_at"]) if row.get("finished_at") is not None else None,
            heartbeat_at=float(row["heartbeat_at"]) if row.get("heartbeat_at") is not None else None,
            lease_expires_at=float(row["lease_expires_at"]) if row.get("lease_expires_at") is not None else None,
            assigned_worker_id=row.get("assigned_worker_id"),
            error_message=row.get("error_message"),
            final_text=row.get("final_text"),
            final_metadata=_json_object(row.get("final_metadata_json")),
            input_tokens=int(row.get("input_tokens") or 0),
            output_tokens=int(row.get("output_tokens") or 0),
            tool_call_count=int(row.get("tool_call_count") or 0),
        )


def _json_value(value: Any, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str) or not value.strip():
        return fallback
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


def _json_object(value: Any) -> dict[str, Any]:
    parsed = _json_value(value, {})
    return parsed if isinstance(parsed, dict) else {}


def _string_tuple(value: Any) -> tuple[str, ...]:
    parsed = _json_value(value, [])
    return tuple(str(item) for item in parsed) if isinstance(parsed, list) else ()


@dataclass
class AgentEvent:
    seq: int
    event_type: str
    payload: dict[str, Any]
    created_at: float = field(default_factory=time.time)

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "AgentEvent":
        return cls(
            seq=int(row["seq"]),
            event_type=str(row["event_type"]),
            payload=row["payload"] if isinstance(row["payload"], dict) else {},
            created_at=float(row["created_at"]),
        )
