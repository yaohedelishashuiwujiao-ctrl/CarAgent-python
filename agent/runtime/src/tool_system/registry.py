from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from typing import Any, Iterable, Mapping, Protocol

from .context import ToolContext
from .permission_handler import PermissionResult
from .preflight import EligibilityStatus, PreflightDecision
from .protocol import ToolCall, ToolOutcomeStatus, ToolResult
from .schema_validation import validate_json_schema


@dataclass(frozen=True)
class ToolCapability:
    """Machine-readable description used by routing and preflight."""

    namespace: str = "general"
    actions: tuple[str, ...] = ()
    entity_types: tuple[str, ...] = ()
    input_modes: tuple[str, ...] = ()
    output_modes: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    positive_examples: tuple[str, ...] = ()
    negative_examples: tuple[str, ...] = ()


@dataclass(frozen=True)
class ToolExecutionPolicy:
    """Execution semantics; enforcement is introduced incrementally by Runtime."""

    risk: str = "low"
    side_effect: str = "none"
    timeout_s: float | None = None
    retryable_outcomes: tuple[str, ...] = ()
    max_attempts: int = 1
    concurrency_pool: str = "tool"
    supports_parallel: bool = False
    supports_batch: bool = False
    max_batch_size: int = 8
    idempotent: bool = True
    cache_policy: str = "none"


@dataclass(frozen=True)
class ToolDependencies:
    services: tuple[str, ...] = ()
    required_config: tuple[str, ...] = ()
    health_probe: str | None = None
    coverage_probe: str | None = None


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: Mapping[str, Any]
    aliases: tuple[str, ...] = ()
    is_read_only: bool = False
    is_destructive: bool = False
    strict: bool = False
    max_result_size_chars: int = 20_000
    capability: ToolCapability = field(default_factory=ToolCapability)
    execution: ToolExecutionPolicy = field(default_factory=ToolExecutionPolicy)
    dependencies: ToolDependencies = field(default_factory=ToolDependencies)
    output_schema: Mapping[str, Any] | None = None
    preflight_checks: tuple[str, ...] = ()
    input_aliases: Mapping[str, str] = field(default_factory=dict)


class Tool(Protocol):
    def spec(self) -> ToolSpec: ...

    def run(self, tool_input: dict[str, Any], context: ToolContext) -> ToolResult: ...

    def run_batch(self, tool_inputs: list[dict[str, Any]], context: ToolContext) -> list[ToolResult]: ...

    def check_permissions(
        self, tool_input: dict[str, Any], context: ToolContext
    ) -> PermissionResult:
        """Check if this tool has permission to run.

        Args:
            tool_input: The input arguments for the tool.
            context: The tool execution context.

        Returns:
            PermissionResult indicating allow, deny, or ask.
        """
        return PermissionResult.allow()


class ToolRegistry:
    def __init__(self, tools: Iterable[Tool] | None = None) -> None:
        self._tools: list[Tool] = []
        self._by_name: dict[str, Tool] = {}
        if tools:
            for tool in tools:
                self.register(tool)

    def register(self, tool: Tool) -> None:
        spec = tool.spec()
        key = spec.name.lower()
        if key in self._by_name:
            raise ValueError(f"duplicate tool name: {spec.name}")
        self._tools.append(tool)
        self._by_name[key] = tool
        for alias in spec.aliases:
            alias_key = alias.lower()
            if alias_key in self._by_name:
                raise ValueError(f"duplicate tool alias: {alias}")
            self._by_name[alias_key] = tool

    def list_specs(self) -> list[ToolSpec]:
        return [tool.spec() for tool in self._tools]

    def get(self, name: str) -> Tool | None:
        return self._by_name.get(name.lower())

    def normalize_call(self, call: ToolCall) -> tuple[ToolCall, dict[str, str]]:
        """Map declared input aliases to canonical fields before validation.

        Aliases are tool-owned contract metadata, not fuzzy Runtime guesses. A
        conflicting alias is left intact so strict schema validation rejects it.
        """
        tool = self.get(call.name)
        if tool is None or not isinstance(call.input, dict):
            return call, {}
        aliases = tool.spec().input_aliases
        if not aliases:
            return call, {}
        normalized = dict(call.input)
        applied: dict[str, str] = {}
        for alias, canonical in aliases.items():
            if alias not in normalized:
                continue
            if canonical in normalized and normalized[canonical] != normalized[alias]:
                continue
            normalized[canonical] = normalized.pop(alias)
            applied[alias] = canonical
        return ToolCall(name=call.name, input=normalized, tool_use_id=call.tool_use_id), applied

    def evaluate_eligibility(self, name: str, context: ToolContext) -> PreflightDecision:
        tool = self.get(name)
        if tool is None:
            return PreflightDecision.reject(
                "UNKNOWN_TOOL",
                f"Unknown tool: {name}",
                disable_tool_for_run=True,
            )
        spec = tool.spec()
        allowed = {item.lower() for item in context.allowed_tools}
        if allowed and spec.name.lower() not in allowed:
            return PreflightDecision.reject(
                "TOOL_NOT_IN_JOB_ALLOWLIST",
                "Tool is not authorized for this job.",
                disable_tool_for_run=True,
            )
        if context.permission_context.blocks_tool(spec.name):
            return PreflightDecision.reject(
                "TOOL_BLOCKED_BY_PERMISSION_CONTEXT",
                "Tool is blocked by the current permission context.",
                disable_tool_for_run=True,
            )
        eligibility_check = getattr(tool, "eligibility", None)
        if callable(eligibility_check):
            try:
                decision = eligibility_check(context)
            except Exception as exc:
                return PreflightDecision.reject(
                    "TOOL_ELIGIBILITY_CHECK_FAILED",
                    "Tool availability could not be established.",
                    retryable=True,
                    diagnostics={"error": str(exc)[:1000]},
                )
            if isinstance(decision, PreflightDecision) and not decision.can_execute:
                return decision
        is_enabled = getattr(tool, "is_enabled", None)
        if callable(is_enabled):
            try:
                if not bool(is_enabled(context)):
                    return PreflightDecision.reject(
                        "TOOL_DISABLED_AT_RUNTIME",
                        "Tool is disabled by the current runtime state.",
                        disable_tool_for_run=True,
                    )
            except Exception as exc:
                return PreflightDecision.reject(
                    "TOOL_ELIGIBILITY_CHECK_FAILED",
                    "Tool availability could not be established.",
                    retryable=True,
                    diagnostics={"error": str(exc)[:1000]},
                )
        return PreflightDecision.allow("TOOL_ELIGIBLE")

    def list_eligible_specs(self, context: ToolContext) -> list[ToolSpec]:
        return [
            tool.spec()
            for tool in self._tools
            if self.evaluate_eligibility(tool.spec().name, context).can_execute
        ]

    def preflight(self, call: ToolCall, context: ToolContext) -> PreflightDecision:
        call, _ = self.normalize_call(call)
        eligibility = self.evaluate_eligibility(call.name, context)
        if not eligibility.can_execute:
            return eligibility
        tool = self.get(call.name)
        assert tool is not None
        spec = tool.spec()
        try:
            validate_json_schema(call.input, spec.input_schema, root_name=spec.name)
        except Exception as exc:
            return PreflightDecision.reject(
                "INPUT_SCHEMA_INVALID",
                str(exc),
                diagnostics={"tool": spec.name},
            )

        tool_preflight = getattr(tool, "preflight", None)
        if callable(tool_preflight):
            try:
                decision = tool_preflight(call.input, context)
            except Exception as exc:
                return PreflightDecision.reject(
                    "TOOL_PREFLIGHT_CHECK_FAILED",
                    f"Tool preflight failed: {exc}",
                    diagnostics={"tool": spec.name},
                )
            if isinstance(decision, PreflightDecision) and not decision.can_execute:
                return decision

        permission_result = tool.check_permissions(call.input, context) if hasattr(tool, "check_permissions") else PermissionResult.allow()
        if permission_result.behavior.value == "deny":
            return PreflightDecision.reject(
                "PERMISSION_POLICY_DENIED",
                permission_result.message or "Permission denied by tool policy.",
                disable_tool_for_run=True,
            )
        if permission_result.behavior.value == "ask":
            return PreflightDecision.approval(
                "APPROVAL_REQUIRED",
                permission_result.message or f"Tool '{spec.name}' requires approval.",
            )
        return PreflightDecision.allow()

    def dispatch(self, call: ToolCall, context: ToolContext) -> ToolResult:
        call, applied_aliases = self.normalize_call(call)
        if applied_aliases:
            context.audit_events.append({
                "event": "tool_input_normalized",
                "job_id": context.job_id,
                "trace_id": context.trace_id,
                "tool_name": call.name,
                "aliases": dict(applied_aliases),
            })
        preflight = self.preflight(call, context)
        if preflight.status == EligibilityStatus.INELIGIBLE:
            context.audit_events.append(
                _audit_event(
                    context,
                    call.name,
                    "deny",
                    preflight.reason_code,
                )
            )
            return ToolResult(
                name=call.name,
                output={
                    "error": preflight.message,
                    "preflight_rejected": True,
                    "reason_code": preflight.reason_code,
                    "alternative_capabilities": list(preflight.alternative_capabilities),
                },
                is_error=True,
                tool_use_id=call.tool_use_id,
                outcome_status=preflight_outcome_status(preflight),
                reason_code=preflight.reason_code,
                retryable=preflight.retryable,
                diagnostics=preflight.diagnostics,
            )
        if preflight.status == EligibilityStatus.NEEDS_APPROVAL and context.permission_handler is None:
            context.audit_events.append(
                _audit_event(
                    context,
                    call.name,
                    "ask",
                    preflight.reason_code,
                )
            )
            return ToolResult(
                name=call.name,
                output={
                    "error": preflight.message,
                    "preflight_rejected": True,
                    "reason_code": preflight.reason_code,
                },
                is_error=True,
                tool_use_id=call.tool_use_id,
                outcome_status=ToolOutcomeStatus.APPROVAL_REQUIRED,
                reason_code=preflight.reason_code,
            )
        tool = self.get(call.name)
        if tool is None:
            return ToolResult(
                name=call.name,
                output={"error": f"unknown tool: {call.name}"},
                is_error=True,
                tool_use_id=call.tool_use_id,
                outcome_status=ToolOutcomeStatus.CAPABILITY_MISMATCH,
                reason_code="UNKNOWN_TOOL",
            )
        spec = tool.spec()
        allowed = {name.lower() for name in context.allowed_tools}
        if allowed and spec.name.lower() not in allowed:
            context.audit_events.append(_audit_event(context, spec.name, "deny", "tool_not_in_job_allowlist"))
            return ToolResult(
                name=spec.name,
                output={"error": "tool is not authorized for this job"},
                is_error=True,
                tool_use_id=call.tool_use_id,
                outcome_status=ToolOutcomeStatus.PERMISSION_DENIED,
                reason_code="TOOL_NOT_IN_JOB_ALLOWLIST",
            )
        context.ensure_tool_allowed(spec.name)
        validate_json_schema(call.input, spec.input_schema, root_name=spec.name)

        # Check permissions before running
        permission_result = tool.check_permissions(call.input, context) if hasattr(tool, 'check_permissions') else PermissionResult.allow()
        if permission_result.behavior.value == "deny":
            context.audit_events.append(_audit_event(context, spec.name, "deny", permission_result.message or "permission_policy"))
            return ToolResult(
                name=spec.name,
                output={"error": permission_result.message or "permission denied"},
                is_error=True,
                tool_use_id=call.tool_use_id,
                outcome_status=ToolOutcomeStatus.PERMISSION_DENIED,
                reason_code="PERMISSION_POLICY_DENIED",
            )
        if permission_result.behavior.value == "ask":
            # Need user interaction
            if context.permission_handler is None:
                # No handler available, deny by default
                return ToolResult(
                    name=spec.name,
                    output={"error": permission_result.message or "permission required but no handler available"},
                    is_error=True,
                    tool_use_id=call.tool_use_id,
                    outcome_status=ToolOutcomeStatus.APPROVAL_REQUIRED,
                    reason_code="APPROVAL_HANDLER_UNAVAILABLE",
                )
            # Call the permission handler
            allowed, _ = context.permission_handler(
                spec.name,
                permission_result.message or f"Tool '{spec.name}' requires permission",
                permission_result.suggestion,
            )
            if not allowed:
                return ToolResult(
                    name=spec.name,
                    output={"error": "permission denied by user"},
                    is_error=True,
                    tool_use_id=call.tool_use_id,
                    outcome_status=ToolOutcomeStatus.PERMISSION_DENIED,
                    reason_code="USER_DENIED_APPROVAL",
                )
            # User allowed - proceed with potentially updated input
            if permission_result.updated_input:
                call = ToolCall(
                    name=call.name,
                    input=permission_result.updated_input,
                    tool_use_id=call.tool_use_id,
                )

        context.audit_events.append(_audit_event(context, spec.name, "allow", "authorized"))
        from .execution import execute_tool_with_policy

        result = execute_tool_with_policy(tool, spec, call, context)
        if result.tool_use_id is None and call.tool_use_id is not None:
            result = ToolResult(
                name=result.name,
                output=result.output,
                is_error=result.is_error,
                tool_use_id=call.tool_use_id,
                content_type=result.content_type,
                outcome_status=result.outcome_status,
                reason_code=result.reason_code,
                retryable=result.retryable,
                diagnostics=result.diagnostics,
            )
        return _enforce_result_size(result, spec.max_result_size_chars)

    def dispatch_batch(self, calls: list[ToolCall], context: ToolContext) -> list[ToolResult]:
        if not calls:
            return []
        tool = self.get(calls[0].name)
        if tool is None:
            return [self.dispatch(call, context) for call in calls]
        spec = tool.spec()
        run_batch = getattr(tool, "run_batch", None)
        if (
            not callable(run_batch)
            or not spec.execution.supports_batch
            or any(call.name.lower() != spec.name.lower() for call in calls)
        ):
            return [self.dispatch(call, context) for call in calls]

        max_batch_size = max(1, int(spec.execution.max_batch_size or 1))
        if len(calls) > max_batch_size:
            results: list[ToolResult] = []
            for start in range(0, len(calls), max_batch_size):
                results.extend(self.dispatch_batch(calls[start:start + max_batch_size], context))
            return results

        normalized_calls = [self.normalize_call(call)[0] for call in calls]
        for call in normalized_calls:
            preflight = self.preflight(call, context)
            if not preflight.can_execute:
                return [self.dispatch(call, context) for call in normalized_calls]
        context.audit_events.append({
            "event": "tool_batch_dispatch_started",
            "job_id": context.job_id,
            "trace_id": context.trace_id,
            "tool_name": spec.name,
            "batch_size": len(normalized_calls),
        })
        started = datetime.now(timezone.utc)
        try:
            raw_results = run_batch([call.input for call in normalized_calls], context)
        except Exception as exc:
            raw_results = [
                ToolResult(
                    name=spec.name,
                    output={"error": str(exc)[:2000]},
                    is_error=True,
                    outcome_status=ToolOutcomeStatus.PERMANENT_FAILURE,
                    reason_code="BATCH_DISPATCH_EXCEPTION",
                )
                for _ in normalized_calls
            ]
        results: list[ToolResult] = []
        for call, result in zip(normalized_calls, raw_results):
            if result.tool_use_id is None and call.tool_use_id is not None:
                result = ToolResult(
                    name=result.name,
                    output=result.output,
                    is_error=result.is_error,
                    tool_use_id=call.tool_use_id,
                    content_type=result.content_type,
                    outcome_status=result.outcome_status,
                    reason_code=result.reason_code,
                    retryable=result.retryable,
                    diagnostics=result.diagnostics,
                )
            results.append(_enforce_result_size(result, spec.max_result_size_chars))
        if len(results) < len(normalized_calls):
            for call in normalized_calls[len(results):]:
                results.append(
                    ToolResult(
                        name=spec.name,
                        output={"error": "batch tool returned fewer results than calls"},
                        is_error=True,
                        tool_use_id=call.tool_use_id,
                        outcome_status=ToolOutcomeStatus.PERMANENT_FAILURE,
                        reason_code="BATCH_RESULT_COUNT_MISMATCH",
                    )
                )
        elif len(results) > len(normalized_calls):
            results = results[:len(normalized_calls)]
        elapsed_ms = round((datetime.now(timezone.utc) - started).total_seconds() * 1000)
        context.audit_events.append({
            "event": "tool_batch_dispatch_completed",
            "job_id": context.job_id,
            "trace_id": context.trace_id,
            "tool_name": spec.name,
            "batch_size": len(normalized_calls),
            "elapsed_ms": elapsed_ms,
        })
        return results


def _enforce_result_size(result: ToolResult, max_chars: int) -> ToolResult:
    """Keep oversized tool payloads from silently flooding model context."""

    limit = max(128, int(max_chars))
    try:
        serialized = json.dumps(result.output, ensure_ascii=False, default=str, separators=(",", ":"))
    except Exception:
        serialized = str(result.output)
    if len(serialized) <= limit:
        return result

    preview_length = max(0, limit - 320)
    output: dict[str, Any] = {
        "truncated": True,
        "reason_code": "RESULT_SIZE_LIMIT",
        "original_size_chars": len(serialized),
        "max_result_size_chars": limit,
        "preview": serialized[:preview_length],
    }
    while preview_length and len(json.dumps(output, ensure_ascii=False, separators=(",", ":"))) > limit:
        preview_length //= 2
        output["preview"] = serialized[:preview_length]

    return ToolResult(
        name=result.name,
        output=output,
        is_error=result.is_error,
        tool_use_id=result.tool_use_id,
        content_type="json",
        outcome_status=(result.outcome_status if result.is_error else ToolOutcomeStatus.PARTIAL_SUCCESS),
        reason_code=result.reason_code or "RESULT_SIZE_LIMIT",
        retryable=result.retryable,
        diagnostics={**dict(result.diagnostics), "original_size_chars": len(serialized)},
    )


def preflight_outcome_status(decision: PreflightDecision) -> ToolOutcomeStatus:
    code = decision.reason_code
    if decision.retryable:
        return ToolOutcomeStatus.TRANSIENT_FAILURE
    if "PERMISSION" in code or "ALLOWLIST" in code or "DATA_SCOPE" in code or "DENIED" in code:
        return ToolOutcomeStatus.PERMISSION_DENIED
    if "DEPENDENCY" in code or "HEALTH" in code:
        return ToolOutcomeStatus.DEPENDENCY_UNHEALTHY
    if "COVERAGE" in code or "FIELD_NOT_COVERED" in code:
        return ToolOutcomeStatus.DATA_COVERAGE_INSUFFICIENT
    if "UNKNOWN_TOOL" in code or "CAPABILITY" in code or "DOMAIN_NOT_ALLOWED" in code:
        return ToolOutcomeStatus.CAPABILITY_MISMATCH
    return ToolOutcomeStatus.INVALID_INPUT


def _audit_event(context: ToolContext, tool_name: str, decision: str, reason: str) -> dict[str, Any]:
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tenant_id": context.tenant_id,
        "user_id": context.user_id,
        "job_id": context.job_id,
        "trace_id": context.trace_id,
        "tool_name": tool_name,
        "authorization_decision": decision,
        "reason": reason,
    }
