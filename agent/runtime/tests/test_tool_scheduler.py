import tempfile
import time
import unittest
from pathlib import Path

from src.tool_system.context import ToolContext
from src.tool_system.protocol import ToolCall, ToolResult
from src.tool_system.registry import ToolExecutionPolicy, ToolSpec, ToolRegistry
from src.tool_system.scheduler import ToolCallScheduler


class CountingTool:
    def __init__(self, *, delay_s: float = 0.0) -> None:
        self.calls = 0
        self.delay_s = delay_s

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="Count",
            description="Count test tool",
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
            },
            is_read_only=True,
            strict=True,
            execution=ToolExecutionPolicy(
                side_effect="none",
                supports_parallel=True,
                idempotent=True,
                concurrency_pool="tool",
            ),
        )

    def run(self, tool_input, context):
        self.calls += 1
        if self.delay_s:
            time.sleep(self.delay_s)
        return ToolResult(name="Count", output={"value": tool_input["value"]})


class BatchCountingTool:
    def __init__(self) -> None:
        self.batch_calls = 0
        self.single_calls = 0

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="BatchCount",
            description="Batch count test tool",
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
            },
            is_read_only=True,
            strict=True,
            execution=ToolExecutionPolicy(
                side_effect="none",
                supports_parallel=True,
                supports_batch=True,
                max_batch_size=8,
                idempotent=True,
                concurrency_pool="tool",
            ),
        )

    def run(self, tool_input, context):
        self.single_calls += 1
        return ToolResult(name="BatchCount", output={"value": tool_input["value"], "mode": "single"})

    def run_batch(self, tool_inputs, context):
        self.batch_calls += 1
        return [
            ToolResult(name="BatchCount", output={"value": item["value"], "mode": "batch"})
            for item in tool_inputs
        ]


class TestToolCallScheduler(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.context = ToolContext(workspace_root=Path(self.temp_dir.name))

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_duplicate_call_is_rejected_before_dispatch(self):
        tool = CountingTool()
        scheduler = ToolCallScheduler(ToolRegistry([tool]), self.context)

        results = scheduler.execute(
            [
                ToolCall(name="Count", input={"value": "a"}, tool_use_id="1"),
                ToolCall(name="Count", input={"value": "a"}, tool_use_id="2"),
            ],
            mode="test",
        )

        self.assertEqual(tool.calls, 1)
        self.assertFalse(results[0].result.is_error)
        self.assertTrue(results[1].result.is_error)
        self.assertEqual(results[1].result.reason_code, "DUPLICATE_TOOL_CALL_IN_BATCH")

    def test_schema_invalid_call_is_rejected_before_dispatch(self):
        tool = CountingTool()
        scheduler = ToolCallScheduler(ToolRegistry([tool]), self.context)

        results = scheduler.execute([ToolCall(name="Count", input={"wrong": "a"}, tool_use_id="1")], mode="test")

        self.assertEqual(tool.calls, 0)
        self.assertTrue(results[0].result.is_error)
        self.assertEqual(results[0].result.reason_code, "INPUT_SCHEMA_INVALID")

    def test_parallel_batch_audit_is_recorded(self):
        tool = CountingTool(delay_s=0.05)
        scheduler = ToolCallScheduler(ToolRegistry([tool]), self.context)

        results = scheduler.execute(
            [
                ToolCall(name="Count", input={"value": "a"}, tool_use_id="1"),
                ToolCall(name="Count", input={"value": "b"}, tool_use_id="2"),
            ],
            mode="test",
        )

        self.assertEqual([item.result.output["value"] for item in results], ["a", "b"])
        events = [item.get("event") for item in self.context.audit_events]
        self.assertIn("tool_scheduler_decision", events)
        self.assertIn("parallel_tool_batch_started", events)
        self.assertIn("parallel_tool_batch_completed", events)

    def test_ledger_records_batch_outcomes(self):
        tool = CountingTool()
        scheduler = ToolCallScheduler(ToolRegistry([tool]), self.context)

        scheduler.execute([ToolCall(name="Count", input={"value": "a"}, tool_use_id="1")], mode="test")

        ledger = self.context.runtime_state["tool_scheduler_ledger"]
        self.assertEqual(ledger["requested"], 1)
        self.assertEqual(ledger["dispatched"], 1)
        self.assertEqual(ledger["rejected"], 0)
        self.assertEqual(ledger["status_counts"]["success"], 1)

    def test_run_dedupe_rejects_call_seen_in_prior_batch(self):
        tool = CountingTool()
        scheduler = ToolCallScheduler(ToolRegistry([tool]), self.context)

        first = scheduler.execute(
            [ToolCall(name="Count", input={"value": "a"}, tool_use_id="1")],
            mode="test",
            dedupe_scope="run",
        )
        second = scheduler.execute(
            [ToolCall(name="Count", input={"value": "a"}, tool_use_id="2")],
            mode="test",
            dedupe_scope="run",
        )

        self.assertFalse(first[0].result.is_error)
        self.assertTrue(second[0].result.is_error)
        self.assertEqual(second[0].result.reason_code, "DUPLICATE_TOOL_CALL_IN_RUN")
        self.assertEqual(tool.calls, 1)

    def test_batchable_homogeneous_calls_use_batch_dispatch(self):
        tool = BatchCountingTool()
        scheduler = ToolCallScheduler(ToolRegistry([tool]), self.context)

        results = scheduler.execute(
            [
                ToolCall(name="BatchCount", input={"value": "a"}, tool_use_id="1"),
                ToolCall(name="BatchCount", input={"value": "b"}, tool_use_id="2"),
            ],
            mode="test",
        )

        self.assertEqual(tool.batch_calls, 1)
        self.assertEqual(tool.single_calls, 0)
        self.assertEqual([item.result.output["value"] for item in results], ["a", "b"])
        self.assertEqual([item.result.tool_use_id for item in results], ["1", "2"])
        events = [item.get("event") for item in self.context.audit_events]
        self.assertIn("batch_tool_group_started", events)
        self.assertIn("tool_batch_dispatch_started", events)
        self.assertIn("tool_batch_dispatch_completed", events)


if __name__ == "__main__":
    unittest.main()
