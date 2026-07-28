from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from evals.run_eval import aggregate, parse_sse, score_case


class EvalHarnessTest(unittest.TestCase):
    def test_parse_sse_extracts_payload_events(self) -> None:
        raw = (
            'event: message\n'
            'data: {"event_type":"tool_use","payload":{"tool":"Read"}}\n\n'
            'data: {"event_type":"message","payload":{"text":"done"}}\n\n'
        )

        self.assertEqual(
            parse_sse(raw),
            [{"type": "tool_use", "tool": "Read"}, {"type": "message", "text": "done"}],
        )

    def test_score_parallel_batch_and_tool_alternative(self) -> None:
        case = {
            "id": "parallel",
            "checks": {
                "terminal_status": ["succeeded"],
                "required_tool_any": ["SubjectsDataCatalogSearch", "SubjectsAttributeLookup"],
                "parallel_batch_min": 1,
            },
        }
        result = {
            "status": "succeeded",
            "events": [{"type": "tool_use", "tool": "SubjectsAttributeLookup"}],
            "final_metadata": {
                "tool_audit": [{"event": "parallel_tool_batch_completed"}],
            },
        }

        scored = score_case(case, result)

        self.assertTrue(scored["passed"])
        self.assertEqual(scored["score"], 1.0)

    def test_score_artifact_from_metadata_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "answer.pptx"
            artifact.write_bytes(b"not-empty")
            case = {
                "id": "artifact",
                "checks": {
                    "terminal_status": ["succeeded"],
                    "artifact_type": "pptx",
                },
            }
            result = {
                "status": "succeeded",
                "events": [],
                "final_metadata": {"artifacts": [{"path": str(artifact)}]},
            }

            scored = score_case(case, result)

        self.assertTrue(scored["passed"])

    def test_score_route_model_and_budget_contract(self) -> None:
        case = {
            "id": "route",
            "checks": {
                "expected_route": "vehicle_spec",
                "expected_model_tier": "standard",
                "expected_budget_class": "lookup",
            },
        }
        result = {
            "status": "succeeded",
            "events": [],
            "final_metadata": {
                "route": "vehicle_spec",
                "model_tier": "standard",
                "budget_class": "lookup",
            },
        }

        scored = score_case(case, result)

        self.assertTrue(scored["passed"])
        self.assertEqual(scored["route"], "vehicle_spec")
        self.assertEqual(scored["model_tier"], "standard")

    def test_aggregate_reports_routing_cost_metrics(self) -> None:
        records = [
            {
                "usage": {"input_tokens": 10, "output_tokens": 5},
                "case": {"difficulty": 1},
                "final_metadata": {"run_budget": {"usage": {"model_turns": 1}}},
                "evaluation": {
                    "passed": True,
                    "score": 1.0,
                    "tool_calls": 1,
                    "invalid_tool_results": 0,
                    "model_tier": "standard",
                    "assertions": [{"name": "expected_route", "passed": True}],
                },
            }
        ]

        metrics = aggregate(records, input_price=0.0, output_price=0.0)

        self.assertEqual(metrics["tool_route_accuracy"], 1.0)
        self.assertEqual(metrics["simple_lookup_strong_model_rate"], 0.0)
        self.assertEqual(metrics["simple_lookup_avg_model_turns"], 1.0)


if __name__ == "__main__":
    unittest.main()
