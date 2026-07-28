from __future__ import annotations

import unittest

from src.tool_system.run_state import AgentRunState


class AgentRunStateTests(unittest.TestCase):
    def test_novel_evidence_is_not_confused_with_goal_progress(self) -> None:
        state = AgentRunState.from_requirements(
            "Research and create a report",
            [{"id": "artifact:report", "description": "Create a report artifact"}],
        )

        for index in range(4):
            state.record_action(
                tool_name="Search",
                tool_input={"query": f"q{index}"},
                outcome_status="success",
                reason_code=None,
                new_evidence_count=1,
                result_is_novel=True,
            )

        self.assertEqual(state.evidence_count, 4)
        self.assertEqual(state.consecutive_without_goal_progress, 4)
        self.assertTrue(state.should_request_replan())

    def test_model_owned_plan_completion_resets_stagnation(self) -> None:
        state = AgentRunState(goal="Create an artifact")
        state.update_plan([
            {
                "content": "Gather evidence",
                "status": "in_progress",
                "activeForm": "Gathering evidence",
                "toolHints": ["Search"],
            },
            {"content": "Generate artifact", "status": "pending", "activeForm": "Generating artifact"},
        ])
        self.assertEqual(state.active_tool_hints, {"Search"})
        state.consecutive_without_goal_progress = 5

        progressed = state.update_plan([
            {"content": "Gather evidence", "status": "completed", "activeForm": "Gathered evidence"},
            {"content": "Generate artifact", "status": "in_progress", "activeForm": "Generating artifact"},
        ])

        self.assertTrue(progressed)
        self.assertEqual(state.consecutive_without_goal_progress, 0)
        self.assertEqual(state.active_step["content"], "Generate artifact")

    def test_repeated_failed_path_requests_replan(self) -> None:
        state = AgentRunState(goal="Analyze data")
        for _ in range(2):
            state.record_action(
                tool_name="SqlQuery",
                tool_input={"sql": "bad"},
                outcome_status="invalid_input",
                reason_code="INVALID_SQL",
            )

        self.assertTrue(state.should_request_replan())
        self.assertIn("failed 2 time(s)", state.prompt())

    def test_new_already_completed_steps_do_not_fake_plan_progress(self) -> None:
        state = AgentRunState(goal="Research")
        progressed = state.update_plan([
            {"content": "A newly invented completed step", "status": "completed", "activeForm": "Done"},
        ])
        self.assertFalse(progressed)

        state.record_action(
            tool_name="TodoWrite",
            tool_input={"todos": []},
            outcome_status="success",
            reason_code=None,
            plan_progress=progressed,
        )
        self.assertEqual(state.consecutive_without_goal_progress, 1)

    def test_two_failed_semantic_replans_reach_stagnation_terminal(self) -> None:
        state = AgentRunState(goal="Research")
        for index in range(6):
            state.record_action(
                tool_name="Search",
                tool_input={"query": str(index)},
                outcome_status="success",
                reason_code=None,
                result_is_novel=True,
            )
            if state.should_request_replan():
                state.mark_replan_requested()
        self.assertEqual(state.replan_requests, 2)
        self.assertTrue(state.should_stop_for_stagnation())


if __name__ == "__main__":
    unittest.main()
