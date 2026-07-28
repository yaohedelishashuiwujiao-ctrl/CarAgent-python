from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ToolExecutionPolicyView:
    timeout_s: float | None
    retryable_outcomes: tuple[str, ...]
    max_attempts: int
    concurrency_pool: str
    idempotent: bool
    side_effect: str

    @classmethod
    def from_spec(cls, spec: object) -> "ToolExecutionPolicyView":
        policy = getattr(spec, "execution")
        return cls(
            timeout_s=getattr(policy, "timeout_s", None),
            retryable_outcomes=tuple(getattr(policy, "retryable_outcomes", ())),
            max_attempts=max(1, min(int(getattr(policy, "max_attempts", 1)), 5)),
            concurrency_pool=str(getattr(policy, "concurrency_pool", "tool") or "tool"),
            idempotent=bool(getattr(policy, "idempotent", True)),
            side_effect=str(getattr(policy, "side_effect", "none") or "none"),
        )
