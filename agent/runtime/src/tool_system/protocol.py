from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal, Mapping, Optional


class ToolOutcomeStatus(str, Enum):
    """Stable Runtime-facing classification for a tool execution outcome."""

    SUCCESS = "success"
    PARTIAL_SUCCESS = "partial_success"
    NO_DATA = "no_data"
    INVALID_INPUT = "invalid_input"
    CAPABILITY_MISMATCH = "capability_mismatch"
    DATA_COVERAGE_INSUFFICIENT = "data_coverage_insufficient"
    PERMISSION_DENIED = "permission_denied"
    APPROVAL_REQUIRED = "approval_required"
    DEPENDENCY_UNHEALTHY = "dependency_unhealthy"
    TRANSIENT_FAILURE = "transient_failure"
    PERMANENT_FAILURE = "permanent_failure"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"


@dataclass(frozen=True)
class ToolCall:
    name: str
    input: dict[str, Any]
    tool_use_id: Optional[str] = None


@dataclass(frozen=True)
class ToolResult:
    name: str
    output: Any
    is_error: bool = False
    tool_use_id: Optional[str] = None
    content_type: Literal["text", "json"] = "json"
    outcome_status: ToolOutcomeStatus = ToolOutcomeStatus.SUCCESS
    reason_code: str | None = None
    retryable: bool = False
    diagnostics: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Preserve compatibility with existing tools that only set is_error.
        if self.is_error and self.outcome_status == ToolOutcomeStatus.SUCCESS:
            object.__setattr__(self, "outcome_status", ToolOutcomeStatus.PERMANENT_FAILURE)
