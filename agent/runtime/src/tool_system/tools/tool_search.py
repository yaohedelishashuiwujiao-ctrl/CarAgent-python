from __future__ import annotations

from typing import Any

from ..context import ToolContext
from ..errors import ToolInputError
from ..protocol import ToolOutcomeStatus, ToolResult
from ..registry import ToolCapability, ToolExecutionPolicy, ToolRegistry, ToolSpec


class ToolSearchTool:
    def __init__(self, registry: ToolRegistry):
        self._registry = registry

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="ToolSearch",
            description=(
                "Search the eligible deferred tool catalog by capability, entity, action, input/output mode, "
                "name, or description. Use this only when the currently exposed tools do not cover the next action."
            ),
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "query": {"type": "string"},
                    "max_results": {"type": "integer"},
                },
                "required": ["query"],
            },
            is_read_only=True,
            max_result_size_chars=100_000,
            strict=True,
            capability=ToolCapability(
                namespace="runtime.discovery",
                actions=("search", "select"),
                entity_types=("tool_capability",),
                input_modes=("capability_query",),
                output_modes=("tool_candidates",),
                limitations=("Returns only tools eligible in the current ToolContext.",),
                positive_examples=("Find a tool that can render a PPTX artifact.",),
                negative_examples=("Do not use when an already exposed tool directly covers the action.",),
            ),
            execution=ToolExecutionPolicy(
                concurrency_pool="tool",
                supports_parallel=False,
                cache_policy="request",
            ),
            preflight_checks=("tool_authorized", "candidate_eligibility"),
        )

    def run(self, tool_input: dict[str, Any], context: ToolContext) -> ToolResult:
        query = tool_input.get("query")
        if not isinstance(query, str) or not query.strip():
            raise ToolInputError("query must be a non-empty string")
        max_results = tool_input.get("max_results", 5)
        if not isinstance(max_results, int) or max_results < 1 or max_results > 50:
            raise ToolInputError("max_results must be an integer between 1 and 50")

        q = query.strip()
        lowered = q.lower()
        if lowered.startswith("select:"):
            name = q.split(":", 1)[1].strip()
            tool = self._registry.get(name)
            eligible = (
                self._registry.evaluate_eligibility(name, context).can_execute
                if tool is not None
                else False
            )
            matches = [tool.spec().name] if tool and eligible and tool.spec().name != "ToolSearch" else []
            return ToolResult(
                name="ToolSearch",
                output={
                    "matches": matches,
                    "query": query,
                    "total_deferred_tools": max(0, len(self._registry.list_eligible_specs(context)) - 1),
                },
                outcome_status=ToolOutcomeStatus.SUCCESS if matches else ToolOutcomeStatus.NO_DATA,
                reason_code=None if matches else "TOOL_DISCOVERY_NO_ELIGIBLE_MATCH",
            )

        query_tokens = _search_tokens(lowered)
        scored: list[tuple[int, str, dict[str, Any]]] = []
        eligible_specs = [spec for spec in self._registry.list_eligible_specs(context) if spec.name != "ToolSearch"]
        for spec in eligible_specs:
            capability = spec.capability
            structured_fields = (
                capability.namespace,
                *capability.actions,
                *capability.entity_types,
                *capability.input_modes,
                *capability.output_modes,
                *capability.positive_examples,
            )
            structured_text = " ".join(structured_fields).lower()
            descriptive_text = f"{spec.name} {spec.description} {' '.join(capability.limitations)}".lower()
            score = 0
            reasons: list[str] = []
            if lowered == spec.name.lower():
                score += 200
                reasons.append("exact_name")
            elif lowered in spec.name.lower():
                score += 100
                reasons.append("name")
            structured_overlap = len(query_tokens & _search_tokens(structured_text))
            descriptive_overlap = len(query_tokens & _search_tokens(descriptive_text))
            if structured_overlap:
                score += structured_overlap * 20
                reasons.append("capability")
            if descriptive_overlap:
                score += descriptive_overlap * 5
                reasons.append("description")
            if lowered in structured_text:
                score += 40
            if score <= 0:
                continue
            summary = {
                "name": spec.name,
                "namespace": capability.namespace,
                "actions": list(capability.actions),
                "entity_types": list(capability.entity_types),
                "output_modes": list(capability.output_modes),
                "limitations": list(capability.limitations),
                "score": score,
                "match_reasons": reasons,
            }
            scored.append((-score, spec.name.lower(), summary))
        scored.sort(key=lambda item: (item[0], item[1]))
        candidates = [summary for _, _, summary in scored[:max_results]]
        matches = [candidate["name"] for candidate in candidates]
        return ToolResult(
            name="ToolSearch",
            output={
                "matches": matches,
                "candidates": candidates,
                "query": query,
                "total_deferred_tools": len(eligible_specs),
            },
            outcome_status=ToolOutcomeStatus.SUCCESS if matches else ToolOutcomeStatus.NO_DATA,
            reason_code=None if matches else "TOOL_DISCOVERY_NO_ELIGIBLE_MATCH",
        )


def _search_tokens(text: str) -> set[str]:
    import re

    latin = set(re.findall(r"[a-z][a-z0-9_.-]{1,}", text.lower()))
    cjk = "".join(re.findall(r"[\u4e00-\u9fff]", text))
    return latin | {cjk[index:index + 2] for index in range(max(0, len(cjk) - 1))}
