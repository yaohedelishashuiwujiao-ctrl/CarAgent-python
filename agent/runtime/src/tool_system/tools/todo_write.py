from __future__ import annotations

from typing import Any

from ..context import ToolContext
from ..errors import ToolInputError
from ..protocol import ToolResult
from ..registry import ToolCapability, ToolExecutionPolicy, ToolSpec


class TodoWriteTool:
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="TodoWrite",
            description="Update the current todo list for this session.",
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "todos": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "content": {"type": "string", "minLength": 1},
                                "status": {
                                    "type": "string",
                                    "enum": ["pending", "in_progress", "completed"],
                                },
                                "activeForm": {"type": "string", "minLength": 1},
                                "expectedOutcome": {"type": "string", "minLength": 1},
                                "toolHints": {
                                    "type": "array",
                                    "items": {"type": "string", "minLength": 1},
                                    "maxItems": 5,
                                },
                            },
                            "required": ["content", "status", "activeForm"],
                        },
                    }
                },
                "required": ["todos"],
            },
            is_read_only=True,
            max_result_size_chars=100_000,
            strict=True,
            capability=ToolCapability(
                namespace="agent.plan",
                actions=("create", "revise", "complete_step"),
                entity_types=("execution_plan",),
                input_modes=("todo_list",),
                output_modes=("plan_state",),
                limitations=("Stores model-authored plan state; it does not execute plan steps.",),
                positive_examples=("Maintain a concise plan for a multi-step autonomous task.",),
                negative_examples=("Do not use for a one-step question that can be answered directly.",),
            ),
            execution=ToolExecutionPolicy(
                risk="low",
                side_effect="runtime_state",
                concurrency_pool="tool",
                supports_parallel=False,
                idempotent=True,
            ),
            preflight_checks=("tool_authorized", "plan_schema"),
        )

    def run(self, tool_input: dict[str, Any], context: ToolContext) -> ToolResult:
        todos = tool_input.get("todos")
        if not isinstance(todos, list):
            raise ToolInputError("todos must be an array")

        old = list(context.todos)
        all_done = True
        normalized: list[dict[str, Any]] = []
        for i, t in enumerate(todos):
            if not isinstance(t, dict):
                raise ToolInputError(f"todos[{i}] must be an object")
            content = t.get("content")
            status = t.get("status")
            active_form = t.get("activeForm")
            expected_outcome = t.get("expectedOutcome")
            tool_hints = t.get("toolHints")
            if not isinstance(content, str) or not content.strip():
                raise ToolInputError(f"todos[{i}].content must be a non-empty string")
            if status not in {"pending", "in_progress", "completed"}:
                raise ToolInputError(f"todos[{i}].status must be pending|in_progress|completed")
            if not isinstance(active_form, str) or not active_form.strip():
                raise ToolInputError(f"todos[{i}].activeForm must be a non-empty string")
            if expected_outcome is not None and (not isinstance(expected_outcome, str) or not expected_outcome.strip()):
                raise ToolInputError(f"todos[{i}].expectedOutcome must be a non-empty string")
            if tool_hints is not None and (
                not isinstance(tool_hints, list)
                or len(tool_hints) > 5
                or any(not isinstance(item, str) or not item.strip() for item in tool_hints)
            ):
                raise ToolInputError(f"todos[{i}].toolHints must be an array of at most 5 non-empty strings")
            all_done = all_done and status == "completed"
            normalized_item = {"content": content, "status": status, "activeForm": active_form}
            if expected_outcome is not None:
                normalized_item["expectedOutcome"] = expected_outcome.strip()
            if tool_hints is not None:
                normalized_item["toolHints"] = [item.strip() for item in tool_hints]
            normalized.append(normalized_item)

        context.todos = [] if all_done else normalized
        return ToolResult(
            name="TodoWrite",
            output={"oldTodos": old, "newTodos": normalized},
        )
