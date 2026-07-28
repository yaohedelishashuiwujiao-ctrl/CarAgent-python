from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping


def _fingerprint(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


@dataclass
class AgentRunState:
    """Small, provider-neutral control state for an autonomous agent run.

    The model owns the plan. Runtime only retains that plan, observes whether
    actions advance it, remembers failed paths, and exposes the state again on
    every model turn. No domain workflow or fixed sequence lives here.
    """

    goal: str
    obligations: tuple[str, ...] = ()
    plan: list[dict[str, Any]] = field(default_factory=list)
    plan_revision: int = 0
    action_count: int = 0
    evidence_count: int = 0
    consecutive_without_goal_progress: int = 0
    consecutive_failures: int = 0
    replan_requests: int = 0
    last_replan_action: int = -100
    failed_paths: dict[str, int] = field(default_factory=dict)
    last_action: dict[str, Any] | None = None
    progress_events: list[dict[str, Any]] = field(default_factory=list)
    evidence_ledger: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_requirements(cls, goal: str, requirements: Iterable[Mapping[str, Any]]) -> "AgentRunState":
        obligations = tuple(
            str(item.get("description") or item.get("id") or "").strip()
            for item in requirements
            if str(item.get("description") or item.get("id") or "").strip()
        )
        return cls(goal=(goal or "").strip(), obligations=obligations)

    @property
    def active_step(self) -> dict[str, Any] | None:
        return next((item for item in self.plan if item.get("status") == "in_progress"), None)

    @property
    def has_plan(self) -> bool:
        return bool(self.plan)

    @property
    def plan_complete(self) -> bool:
        return bool(self.plan) and all(item.get("status") == "completed" for item in self.plan)

    @property
    def active_tool_hints(self) -> set[str]:
        step = self.active_step or {}
        hints = step.get("toolHints")
        if not isinstance(hints, list):
            return set()
        return {str(item).strip() for item in hints if str(item).strip()}

    def update_plan(self, todos: Any) -> bool:
        """Persist a model-authored TodoWrite plan and report real plan progress."""
        if not isinstance(todos, list):
            return False
        normalized: list[dict[str, Any]] = []
        for item in todos:
            if not isinstance(item, dict):
                continue
            content = str(item.get("content") or "").strip()
            status = str(item.get("status") or "pending").strip()
            active_form = str(item.get("activeForm") or content).strip()
            if not content or status not in {"pending", "in_progress", "completed"}:
                continue
            normalized_item: dict[str, Any] = {
                "content": content,
                "status": status,
                "activeForm": active_form,
            }
            expected_outcome = item.get("expectedOutcome")
            if isinstance(expected_outcome, str) and expected_outcome.strip():
                normalized_item["expectedOutcome"] = expected_outcome.strip()
            tool_hints = item.get("toolHints")
            if isinstance(tool_hints, list):
                normalized_item["toolHints"] = [str(value).strip() for value in tool_hints if str(value).strip()][:5]
            normalized.append(normalized_item)
        if not normalized:
            return False

        old_by_content = {item["content"]: item["status"] for item in self.plan}
        newly_completed = [
            item["content"]
            for item in normalized
            if (
                item["status"] == "completed"
                and item["content"] in old_by_content
                and old_by_content.get(item["content"]) != "completed"
            )
        ]
        changed = _fingerprint(normalized) != _fingerprint(self.plan)
        if changed:
            self.plan = normalized
            self.plan_revision += 1
        if newly_completed:
            self.consecutive_without_goal_progress = 0
            self.progress_events.append({"kind": "plan_steps_completed", "steps": newly_completed})
        return bool(newly_completed)

    def record_action(
        self,
        *,
        tool_name: str,
        tool_input: Mapping[str, Any] | None,
        outcome_status: str,
        reason_code: str | None,
        requirement_changes: Iterable[str] = (),
        new_evidence_count: int = 0,
        result_is_novel: bool = False,
        plan_progress: bool = False,
    ) -> bool:
        """Record one dispatched action and distinguish novelty from goal progress."""
        self.action_count += 1
        changes = [str(item) for item in requirement_changes if str(item)]
        goal_progress = bool(changes or plan_progress)
        self.evidence_count += max(0, int(new_evidence_count))
        failed = outcome_status not in {"success", "partial_success"}
        self.consecutive_failures = self.consecutive_failures + 1 if failed else 0

        path_key = f"{tool_name}:{_fingerprint(tool_input or {})}"
        if failed:
            self.failed_paths[path_key] = self.failed_paths.get(path_key, 0) + 1
        if goal_progress:
            self.consecutive_without_goal_progress = 0
            self.progress_events.append(
                {"kind": "goal_progress", "tool": tool_name, "requirements": changes}
            )
        else:
            # Novel evidence is useful context, but it is not by itself proof that
            # the task plan or an obligation advanced. Plan creation/churn is also
            # not progress unless a previously recorded step actually completed.
            self.consecutive_without_goal_progress += 1

        self.last_action = {
            "tool": tool_name,
            "outcome": outcome_status,
            "reason_code": reason_code,
            "novel_result": bool(result_is_novel),
            "new_evidence_count": max(0, int(new_evidence_count)),
            "goal_progress": goal_progress,
            "path": path_key,
        }
        return goal_progress

    def add_evidence(self, citations: Iterable[Mapping[str, Any]]) -> None:
        known = {
            str(item.get("evidence_hash") or item.get("citation_id") or "")
            for item in self.evidence_ledger
        }
        for citation in citations:
            key = str(citation.get("evidence_hash") or citation.get("citation_id") or "")
            if not key or key in known:
                continue
            known.add(key)
            content = str(citation.get("content") or "").strip().replace("\n", " ")
            source = str(
                citation.get("source")
                or citation.get("url")
                or citation.get("document_name")
                or citation.get("query")
                or ""
            ).strip()
            self.evidence_ledger.append(
                {
                    "citation_id": citation.get("citation_id"),
                    "source_type": str(citation.get("source_type") or ""),
                    "source": source[:240],
                    "content": content[:360],
                    "evidence_hash": key,
                }
            )
        # Keep a bounded durable ledger. Full evidence remains in final metadata.
        if len(self.evidence_ledger) > 40:
            self.evidence_ledger = self.evidence_ledger[-40:]

    def should_request_replan(self) -> bool:
        if self.action_count - self.last_replan_action < 2:
            return False
        repeated_dead_path = any(count >= 2 for count in self.failed_paths.values())
        return (
            self.consecutive_without_goal_progress >= 4
            or self.consecutive_failures >= 2
            or repeated_dead_path
        )

    def mark_replan_requested(self) -> None:
        self.replan_requests += 1
        self.last_replan_action = self.action_count

    def should_stop_for_stagnation(self) -> bool:
        return (
            self.replan_requests >= 2
            and self.consecutive_without_goal_progress >= 6
        ) or self.consecutive_failures >= 4

    def replan_prompt(self) -> str:
        return (
            "Runtime progress check: recent actions did not advance the model-owned plan or an open task obligation. "
            "Review the run-state snapshot, stop the current low-yield path, and choose a materially different next action. "
            "For a multi-step task, call TodoWrite to create or revise the plan before continuing. Do not mark a step "
            "completed unless its result or required artifact exists."
        )

    def prompt(self) -> str:
        plan_lines = [
            (
                f"- [{item['status']}] {item['content']}"
                + (f"; expected={item['expectedOutcome']}" if item.get("expectedOutcome") else "")
                + (f"; tool_hints={','.join(item['toolHints'])}" if item.get("toolHints") else "")
            )
            for item in self.plan[:12]
        ] or ["- No model-authored plan has been recorded yet."]
        failed_summary = [
            f"- {key} failed {count} time(s)"
            for key, count in sorted(self.failed_paths.items(), key=lambda item: (-item[1], item[0]))[:5]
        ] or ["- none"]
        evidence_summary = [
            (
                f"- [{item.get('citation_id')}] {item.get('source_type')}: "
                f"{item.get('content') or item.get('source')}"
            )
            for item in self.evidence_ledger[-12:]
        ] or ["- none"]
        obligations = [f"- {item}" for item in self.obligations] or ["- none"]
        return "\n".join(
            [
                "Runtime run-state ledger (the model owns and may revise the plan):",
                f"Goal: {self.goal}",
                "Open/completion obligations known at run start:",
                *obligations,
                f"Plan revision: {self.plan_revision}",
                "Current plan:",
                *plan_lines,
                (
                    "Progress counters: "
                    f"actions={self.action_count}, evidence_items={self.evidence_count}, "
                    f"actions_without_goal_progress={self.consecutive_without_goal_progress}, "
                    f"consecutive_failures={self.consecutive_failures}"
                ),
                "Known failed action paths:",
                *failed_summary,
                "Durable evidence summary (survives context compaction):",
                *evidence_summary,
            ]
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "obligations": list(self.obligations),
            "plan": list(self.plan),
            "plan_revision": self.plan_revision,
            "action_count": self.action_count,
            "evidence_count": self.evidence_count,
            "consecutive_without_goal_progress": self.consecutive_without_goal_progress,
            "consecutive_failures": self.consecutive_failures,
            "replan_requests": self.replan_requests,
            "failed_paths": dict(self.failed_paths),
            "last_action": dict(self.last_action) if self.last_action else None,
            "progress_events": list(self.progress_events),
            "evidence_ledger": list(self.evidence_ledger),
        }
