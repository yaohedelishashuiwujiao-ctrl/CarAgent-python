from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from .protocol import ToolOutcomeStatus, ToolResult


LOW_YIELD_STATUSES = {
    ToolOutcomeStatus.NO_DATA.value,
    ToolOutcomeStatus.INVALID_INPUT.value,
    ToolOutcomeStatus.CAPABILITY_MISMATCH.value,
    ToolOutcomeStatus.DATA_COVERAGE_INSUFFICIENT.value,
    ToolOutcomeStatus.PERMISSION_DENIED.value,
    ToolOutcomeStatus.APPROVAL_REQUIRED.value,
    ToolOutcomeStatus.DEPENDENCY_UNHEALTHY.value,
    ToolOutcomeStatus.TRANSIENT_FAILURE.value,
    ToolOutcomeStatus.PERMANENT_FAILURE.value,
    ToolOutcomeStatus.TIMEOUT.value,
}


@dataclass
class RunBudget:
    """Runtime cost/progress accounting for one agent run.

    This is deliberately separate from context-window compaction. It tracks
    money/time risk signals and exposes them for routing, degradation, and eval.
    """

    max_input_tokens: int = 240_000
    max_output_tokens: int = 24_000
    max_tokens_after_progress: int = 80_000
    max_low_yield_actions: int = 3
    input_tokens: int = 0
    output_tokens: int = 0
    model_turns: int = 0
    tool_requested: int = 0
    tool_dispatched: int = 0
    tool_rejected: int = 0
    low_yield_actions: int = 0
    tokens_at_last_progress: int = 0
    events: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_env(cls) -> "RunBudget":
        return cls(
            max_input_tokens=_env_int("CLAWD_RUN_BUDGET_MAX_INPUT_TOKENS", 240_000),
            max_output_tokens=_env_int("CLAWD_RUN_BUDGET_MAX_OUTPUT_TOKENS", 24_000),
            max_tokens_after_progress=_env_int("CLAWD_RUN_BUDGET_MAX_TOKENS_AFTER_PROGRESS", 80_000),
            max_low_yield_actions=_env_int("CLAWD_RUN_BUDGET_MAX_LOW_YIELD_ACTIONS", 3),
        )

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def tokens_after_last_progress(self) -> int:
        return max(0, self.total_tokens - self.tokens_at_last_progress)

    def record_model_turn(self, usage: dict[str, Any] | None) -> None:
        self.model_turns += 1
        if usage:
            self.input_tokens += max(0, int(usage.get("input_tokens") or 0))
            self.output_tokens += max(0, int(usage.get("output_tokens") or 0))
        self._append_event({"event": "model_turn", "turn": self.model_turns, "usage": dict(usage or {})})

    def record_scheduler_ledger(self, ledger: dict[str, Any] | None) -> None:
        if not isinstance(ledger, dict):
            return
        self.tool_requested = max(self.tool_requested, int(ledger.get("requested") or 0))
        self.tool_dispatched = max(self.tool_dispatched, int(ledger.get("dispatched") or 0))
        self.tool_rejected = max(self.tool_rejected, int(ledger.get("rejected") or 0))

    def record_tool_result(self, result: ToolResult, *, made_progress: bool) -> None:
        status = result.outcome_status.value
        low_yield = (status in LOW_YIELD_STATUSES) or bool(result.is_error)
        if made_progress:
            self.low_yield_actions = 0
            self.tokens_at_last_progress = self.total_tokens
        elif low_yield:
            self.low_yield_actions += 1
        self._append_event(
            {
                "event": "tool_result",
                "tool": result.name,
                "outcome_status": status,
                "reason_code": result.reason_code,
                "made_progress": made_progress,
                "low_yield": low_yield,
                "low_yield_actions": self.low_yield_actions,
                "tokens_after_last_progress": self.tokens_after_last_progress,
            }
        )

    def should_degrade(self) -> tuple[bool, str]:
        if self.input_tokens >= self.max_input_tokens:
            return True, "input_token_budget_exceeded"
        if self.output_tokens >= self.max_output_tokens:
            return True, "output_token_budget_exceeded"
        if self.tokens_after_last_progress >= self.max_tokens_after_progress:
            return True, "tokens_after_last_progress_exceeded"
        if self.low_yield_actions >= self.max_low_yield_actions:
            return True, "low_yield_tool_actions_exceeded"
        return False, ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "limits": {
                "max_input_tokens": self.max_input_tokens,
                "max_output_tokens": self.max_output_tokens,
                "max_tokens_after_progress": self.max_tokens_after_progress,
                "max_low_yield_actions": self.max_low_yield_actions,
            },
            "usage": {
                "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
                "total_tokens": self.total_tokens,
                "tokens_after_last_progress": self.tokens_after_last_progress,
                "model_turns": self.model_turns,
            },
            "tools": {
                "requested": self.tool_requested,
                "dispatched": self.tool_dispatched,
                "rejected": self.tool_rejected,
                "low_yield_actions": self.low_yield_actions,
            },
            "events": list(self.events[-20:]),
        }

    def _append_event(self, event: dict[str, Any]) -> None:
        self.events.append(event)
        if len(self.events) > 80:
            del self.events[:-80]


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except ValueError:
        return default

