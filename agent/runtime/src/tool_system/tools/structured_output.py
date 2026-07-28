from __future__ import annotations

from typing import Any

from ..context import ToolContext
from ..protocol import ToolResult
from ..registry import ToolCapability, ToolDependencies, ToolExecutionPolicy, ToolSpec


class StructuredOutputTool:
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="StructuredOutput",
            description="Return a final response as structured JSON.",
            input_schema={"type": "object", "additionalProperties": True},
            is_read_only=True,
            max_result_size_chars=100_000,
            capability=ToolCapability(
                namespace="output.structured",
                actions=("deliver",),
                entity_types=("structured_response",),
                input_modes=("json_object",),
                output_modes=("json",),
                limitations=("The current tool accepts arbitrary JSON unless an OutputContract supplies a schema.",),
            ),
            execution=ToolExecutionPolicy(
                side_effect="runtime_outbox",
                concurrency_pool="tool",
                supports_parallel=False,
                idempotent=False,
            ),
            dependencies=ToolDependencies(services=("runtime_outbox",)),
            preflight_checks=("tool_authorized", "output_contract_schema_if_present"),
        )

    def run(self, tool_input: dict[str, Any], context: ToolContext) -> ToolResult:
        context.outbox.append({"tool": "StructuredOutput", "structured_output": tool_input})
        return ToolResult(
            name="StructuredOutput",
            output={
                "data": "Structured output provided successfully",
                "structured_output": tool_input,
            },
        )
