import unittest

from src.task_router import build_subjects_lookup_answer, build_subjects_stats_answer, classify_l0
from src.routing_decision import decide_route
from src.tool_system.protocol import ToolOutcomeStatus, ToolResult


class TestTaskRouter(unittest.TestCase):
    def test_single_vehicle_attribute_query_routes_to_deterministic_workflow(self):
        route = classify_l0("查询平台数据中小米SU7的轴距，给出数值、单位和数据来源。")

        self.assertEqual(route.task_type, "single_vehicle_attribute_query")
        self.assertTrue(route.deterministic)
        self.assertEqual(route.entities, ("小米SU7",))
        self.assertEqual(route.attributes, ("轴距",))

    def test_field_catalog_query_routes_to_deterministic_workflow(self):
        route = classify_l0("查询字段目录中的轴距和整备质量定义、单位和覆盖情况")

        self.assertEqual(route.task_type, "field_catalog_query")
        self.assertTrue(route.deterministic)
        self.assertEqual(route.attributes, ("轴距", "整备质量"))

    def test_complex_analysis_stays_on_agent_loop(self):
        route = classify_l0("帮我做一个悬架系统配置调研并生成6页PPT")

        self.assertFalse(route.deterministic)
        self.assertEqual(route.recommended_path, "agent_loop")

    def test_cohort_attribute_query_routes_without_business_field_mapping(self):
        route = classify_l0("帮我调研全部MPV具备前备箱的情况。")

        self.assertEqual(route.task_type, "cohort_attribute_query")
        self.assertTrue(route.deterministic)
        self.assertEqual(route.entities, ("MPV",))
        self.assertEqual(route.attributes, ("前备箱",))
        self.assertNotIn("级别=", route.entities[0])

    def test_vehicle_attribute_stats_routes_to_deterministic_workflow(self):
        route = classify_l0("全部车型的前后轮距统计分析")

        self.assertEqual(route.task_type, "vehicle_attribute_stats")
        self.assertTrue(route.deterministic)
        self.assertEqual(route.attributes, ("前轮距", "后轮距"))

    def test_build_subjects_lookup_answer_formats_value_and_metadata(self):
        route = classify_l0("查小鹏X9的轴距")
        result = ToolResult(
            name="SubjectsAttributeLookup",
            output={
                "entity_keyword": "小鹏X9",
                "attribute_keyword": "轴距",
                "rows": [
                    {
                        "vehicle_name": "小鹏 X9",
                        "attribute_name": "轴距",
                        "attribute_code": "wheelbase",
                        "value_number": 3160,
                        "unit": "mm",
                    }
                ],
            },
            outcome_status=ToolOutcomeStatus.SUCCESS,
        )

        answer = build_subjects_lookup_answer(route, [result])

        self.assertIn("3160 mm", answer["text"])
        self.assertEqual(answer["task_contract_status"], "satisfied")
        self.assertEqual(answer["citations"][0]["metadata"]["row_count"], 1)

    def test_build_subjects_stats_answer_formats_aggregates(self):
        route = classify_l0("全部车型的前后轮距统计分析")
        result = ToolResult(
            name="SubjectsAttributeStats",
            output={
                "entity_keyword": "*",
                "attribute_keywords": ["前轮距"],
                "populated_numeric_value_count": 2,
                "results": [
                    {
                        "attribute_keyword": "前轮距",
                        "stats": [
                            {
                                "attribute_name": "前轮距(mm)",
                                "attribute_unit": "mm",
                                "numeric_value_count": 2,
                                "avg_value": 1700,
                                "min_value": 1600,
                                "max_value": 1800,
                                "stddev_pop": 100,
                            }
                        ],
                    }
                ],
            },
            outcome_status=ToolOutcomeStatus.SUCCESS,
        )

        answer = build_subjects_stats_answer(route, [result])

        self.assertIn("前轮距(mm)", answer["text"])
        self.assertIn("1700 mm", answer["text"])
        self.assertEqual(answer["task_contract_status"], "satisfied")

    def test_route_decision_uses_zero_model_for_l0_lookup(self):
        decision = decide_route("查询小鹏X9的轴距")

        self.assertEqual(decision.execution_path, "deterministic_workflow")
        self.assertEqual(decision.model_tier, "cheap")
        self.assertEqual(decision.budget_class, "lookup")
        self.assertEqual(decision.estimated_cost, 1)

    def test_route_decision_assigns_artifact_budget(self):
        decision = decide_route("帮我生成一份竞品趋势分析PPT")

        self.assertEqual(decision.route, "artifact_generation")
        self.assertEqual(decision.model_tier, "strong")
        self.assertEqual(decision.budget_class, "artifact")
        self.assertEqual(decision.estimated_cost, 8)


if __name__ == "__main__":
    unittest.main()
