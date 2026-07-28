from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


class EligibilityStatus(str, Enum):
    ELIGIBLE = "eligible"
    INELIGIBLE = "ineligible"
    NEEDS_APPROVAL = "needs_approval"
    NEEDS_DISCOVERY = "needs_discovery"


@dataclass(frozen=True)
class PreflightDecision:
    status: EligibilityStatus
    reason_code: str
    message: str
    retryable: bool = False
    disable_tool_for_run: bool = False
    alternative_capabilities: tuple[str, ...] = ()
    diagnostics: Mapping[str, Any] = field(default_factory=dict)

    @property
    def can_execute(self) -> bool:
        return self.status == EligibilityStatus.ELIGIBLE

    @classmethod
    def allow(cls, reason_code: str = "PREFLIGHT_PASSED") -> "PreflightDecision":
        return cls(EligibilityStatus.ELIGIBLE, reason_code, "Tool call passed preflight checks.")

    @classmethod
    def reject(
        cls,
        reason_code: str,
        message: str,
        *,
        retryable: bool = False,
        disable_tool_for_run: bool = False,
        alternative_capabilities: tuple[str, ...] = (),
        diagnostics: Mapping[str, Any] | None = None,
    ) -> "PreflightDecision":
        return cls(
            EligibilityStatus.INELIGIBLE,
            reason_code,
            message,
            retryable=retryable,
            disable_tool_for_run=disable_tool_for_run,
            alternative_capabilities=alternative_capabilities,
            diagnostics=diagnostics or {},
        )

    @classmethod
    def approval(
        cls,
        reason_code: str,
        message: str,
        *,
        diagnostics: Mapping[str, Any] | None = None,
    ) -> "PreflightDecision":
        return cls(
            EligibilityStatus.NEEDS_APPROVAL,
            reason_code,
            message,
            diagnostics=diagnostics or {},
        )
