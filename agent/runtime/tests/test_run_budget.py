import unittest

from src.tool_system.protocol import ToolOutcomeStatus, ToolResult
from src.tool_system.run_budget import RunBudget


class RunBudgetTests(unittest.TestCase):
    def test_records_model_usage_and_tokens_after_progress(self):
        budget = RunBudget(max_tokens_after_progress=100)

        budget.record_model_turn({"input_tokens": 40, "output_tokens": 10})
        self.assertEqual(budget.total_tokens, 50)
        self.assertEqual(budget.tokens_after_last_progress, 50)

        budget.record_tool_result(
            ToolResult(name="Search", output={}, outcome_status=ToolOutcomeStatus.SUCCESS),
            made_progress=True,
        )
        self.assertEqual(budget.tokens_after_last_progress, 0)

        budget.record_model_turn({"input_tokens": 60, "output_tokens": 5})
        self.assertEqual(budget.tokens_after_last_progress, 65)

    def test_low_yield_actions_trigger_degrade(self):
        budget = RunBudget(max_low_yield_actions=2)

        for _ in range(2):
            budget.record_tool_result(
                ToolResult(
                    name="Lookup",
                    output={"error": "bad"},
                    is_error=True,
                    outcome_status=ToolOutcomeStatus.INVALID_INPUT,
                    reason_code="INPUT_SCHEMA_INVALID",
                ),
                made_progress=False,
            )

        degrade, reason = budget.should_degrade()
        self.assertTrue(degrade)
        self.assertEqual(reason, "low_yield_tool_actions_exceeded")

    def test_scheduler_ledger_updates_tool_totals(self):
        budget = RunBudget()

        budget.record_scheduler_ledger({"requested": 3, "dispatched": 2, "rejected": 1})

        payload = budget.as_dict()
        self.assertEqual(payload["tools"]["requested"], 3)
        self.assertEqual(payload["tools"]["dispatched"], 2)
        self.assertEqual(payload["tools"]["rejected"], 1)


if __name__ == "__main__":
    unittest.main()
