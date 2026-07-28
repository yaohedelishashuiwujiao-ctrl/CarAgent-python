from __future__ import annotations

import io
import json
import os
import socket
import threading
import tempfile
import time
import unittest
import urllib.request
import zipfile
from pathlib import Path
from unittest.mock import patch

from src.tool_system.context import ToolContext
from src.tool_system.defaults import build_default_registry
from src.tool_system.protocol import ToolCall, ToolOutcomeStatus, ToolResult
from src.tool_system.registry import ToolExecutionPolicy, ToolRegistry, ToolSpec
from src.tool_system.task_contract import OutputContract, TaskRequirementState
from src.tool_system.tools import (
    AskUserQuestionTool,
    AutoChartGenerateTool,
    AutoPptxGenerateTool,
    BashTool,
    BriefTool,
    ConfigTool,
    CronCreateTool,
    CronDeleteTool,
    CronListTool,
    FileEditTool,
    FileReadTool,
    FileWriteTool,
    GlobTool,
    GrepTool,
    KnowledgeFetchTool,
    KnowledgeSearchTool,
    LSPTool,
    MCPTool,
    ListMcpResourcesTool,
    ReadMcpResourceTool,
    SkillTool,
    SleepTool,
    TodoWriteTool,
    StructuredOutputTool,
    SubjectsAttributeLookupTool,
    SubjectsAttributeStatsTool,
    SubjectsDataCatalogSearchTool,
    SubjectsSqlQueryTool,
    TaskStopTool,
    TaskCreateTool,
    TaskGetTool,
    TaskListTool,
    TaskOutputTool,
    TaskUpdateTool,
    ToolSearchTool,
    WebFetchTool,
    WebSearchTool,
    TeamCreateTool,
    TeamDeleteTool,
    EnterWorktreeTool,
    ExitWorktreeTool,
    EnterPlanModeTool,
    ExitPlanModeTool,
)


class ToolSystemTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.ctx = ToolContext(workspace_root=self.root)

    def tearDown(self) -> None:
        self.tmp.cleanup()


class TestReadTool(ToolSystemTests):
    def test_read_returns_cat_n_format(self) -> None:
        p = self.root / "a.txt"
        p.write_text("line1\nline2\nline3\n", encoding="utf-8")
        tool = FileReadTool()
        out = tool.run({"file_path": str(p), "offset": 2, "limit": 2}, self.ctx).output
        self.assertEqual(out["type"], "text")
        self.assertEqual(out["file"]["content"], "2\tline2\n3\tline3")

    def test_read_allows_relative_path_under_workspace(self) -> None:
        p = self.root / "a.txt"
        p.write_text("x\n", encoding="utf-8")
        tool = FileReadTool()
        out = tool.run({"file_path": "a.txt", "limit": 10}, self.ctx).output
        self.assertEqual(out["type"], "text")
        self.assertIn("1\tx", out["file"]["content"])

    def test_read_returns_file_unchanged_stub(self) -> None:
        p = self.root / "same.txt"
        p.write_text("line\n", encoding="utf-8")
        tool = FileReadTool()
        first = tool.run({"file_path": str(p), "limit": 10}, self.ctx).output
        self.assertEqual(first["type"], "text")
        second = tool.run({"file_path": str(p)}, self.ctx).output
        self.assertEqual(second["type"], "file_unchanged")

    def test_read_notebook(self) -> None:
        p = self.root / "nb.ipynb"
        p.write_text('{"cells":[{"cell_type":"markdown","source":["hi"]}]}', encoding="utf-8")
        out = FileReadTool().run({"file_path": str(p)}, self.ctx).output
        self.assertEqual(out["type"], "notebook")
        self.assertEqual(len(out["file"]["cells"]), 1)

    def test_read_pdf(self) -> None:
        p = self.root / "x.pdf"
        p.write_bytes(b"%PDF-1.4\n1 0 obj\n")
        out = FileReadTool().run({"file_path": str(p)}, self.ctx).output
        self.assertEqual(out["type"], "pdf")

    def test_read_blocks_device_paths(self) -> None:
        with self.assertRaises(Exception):
            FileReadTool().run({"file_path": "/dev/zero"}, self.ctx)


class TestWriteTool(ToolSystemTests):
    def test_write_creates_file(self) -> None:
        tool = FileWriteTool()
        p = self.root / "b.txt"
        out = tool.run({"file_path": str(p), "content": "hello"}, self.ctx).output
        self.assertTrue(p.exists())
        self.assertEqual(out["type"], "create")
        self.assertEqual(out["filePath"], str(p))

    def test_write_requires_read_before_overwrite(self) -> None:
        p = self.root / "c.txt"
        p.write_text("old", encoding="utf-8")
        tool = FileWriteTool()
        with self.assertRaises(Exception):
            tool.run({"file_path": str(p), "content": "new"}, self.ctx)

        FileReadTool().run({"file_path": str(p), "limit": 10}, self.ctx)
        tool.run({"file_path": str(p), "content": "new"}, self.ctx)
        self.assertEqual(p.read_text(encoding="utf-8"), "new")

    def test_write_blocks_docs_by_default(self) -> None:
        """Writing .md files should require permission when allow_docs is False."""
        tool = FileWriteTool()
        p = self.root / "README.md"
        # Permission check should return 'ask' behavior
        result = tool.check_permissions({"file_path": str(p), "content": "x"}, self.ctx)
        self.assertEqual(result.behavior.value, "ask")
        # But run() itself should NOT raise - it just proceeds (permission is checked elsewhere)
        # Note: run() will still succeed because permission checking moved to check_permissions()


class TestEditTool(ToolSystemTests):
    def test_edit_requires_read(self) -> None:
        p = self.root / "d.txt"
        p.write_text("hello world", encoding="utf-8")
        tool = FileEditTool()
        with self.assertRaises(Exception):
            tool.run({"file_path": str(p), "old_string": "world", "new_string": "you"}, self.ctx)

    def test_edit_replaces_unique(self) -> None:
        p = self.root / "e.txt"
        p.write_text("hello world", encoding="utf-8")
        FileReadTool().run({"file_path": str(p), "limit": 10}, self.ctx)
        out = FileEditTool().run({"file_path": str(p), "old_string": "world", "new_string": "you"}, self.ctx).output
        self.assertEqual(out["filePath"], str(p))
        self.assertEqual(out["replaceAll"], False)
        self.assertEqual(p.read_text(encoding="utf-8"), "hello you")

    def test_edit_requires_replace_all_for_non_unique(self) -> None:
        p = self.root / "f.txt"
        p.write_text("a a a", encoding="utf-8")
        FileReadTool().run({"file_path": str(p), "limit": 10}, self.ctx)
        with self.assertRaises(Exception):
            FileEditTool().run({"file_path": str(p), "old_string": "a", "new_string": "b"}, self.ctx)
        FileEditTool().run({"file_path": str(p), "old_string": "a", "new_string": "b", "replace_all": True}, self.ctx)
        self.assertEqual(p.read_text(encoding="utf-8"), "b b b")


class TestGlobTool(ToolSystemTests):
    def test_glob_sorts_by_mtime(self) -> None:
        a = self.root / "x1.py"
        b = self.root / "x2.py"
        a.write_text("a", encoding="utf-8")
        time.sleep(0.01)
        b.write_text("b", encoding="utf-8")
        out = GlobTool().run({"pattern": "*.py", "path": str(self.root), "limit": 10}, self.ctx).output
        self.assertEqual(out["filenames"][0], str(b))
        self.assertEqual(out["filenames"][1], str(a))


class TestGrepTool(ToolSystemTests):
    def test_grep_files_with_matches(self) -> None:
        (self.root / "a.txt").write_text("hello\nworld\n", encoding="utf-8")
        (self.root / "b.txt").write_text("nope\n", encoding="utf-8")
        out = GrepTool().run({"pattern": "hello", "path": str(self.root)}, self.ctx).output
        self.assertEqual(out["mode"], "files_with_matches")
        self.assertEqual(out["numFiles"], 1)
        self.assertIn("a.txt", out["filenames"][0])

    def test_grep_content_mode_with_line_numbers(self) -> None:
        (self.root / "a.txt").write_text("hello\nhello\n", encoding="utf-8")
        out = GrepTool().run({"pattern": "hello", "path": str(self.root), "output_mode": "content", "-n": True}, self.ctx).output
        self.assertIn(":1:", out["content"])


class TestBashTool(ToolSystemTests):
    def test_bash_echo(self) -> None:
        out = BashTool().run({"command": "echo hello"}, self.ctx).output
        self.assertEqual(out["exit_code"], 0)
        self.assertIn("hello", out["stdout"])

    def test_bash_blocks_sudo(self) -> None:
        with self.assertRaises(Exception):
            BashTool().run({"command": "sudo echo nope"}, self.ctx)


class TestWebFetchTool(ToolSystemTests):
    def test_web_fetch_blocks_file_scheme(self) -> None:
        with self.assertRaises(Exception):
            WebFetchTool().run({"url": "file:///etc/passwd"}, self.ctx)

    def test_web_fetch_extracts_text(self) -> None:
        html_doc = '<html><body><h1>Title</h1><p>Hello <b>world</b></p><a href="/x9_2026/configuration.html">X9配置</a></body></html>'

        class _Resp(io.BytesIO):
            headers = {"Content-Type": "text/html"}

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        with patch.object(socket, "getaddrinfo", return_value=[(None, None, None, None, ("93.184.216.34", 0))]):
            with patch.object(urllib.request, "urlopen", return_value=_Resp(html_doc.encode("utf-8"))):
                out = WebFetchTool().run({"url": "https://car.autohome.com.cn/"}, self.ctx).output
                self.assertIn("Title", out["content"])
                self.assertIn("Hello world", out["content"])
                self.assertIn("https://car.autohome.com.cn/x9_2026/configuration.html", out["content"])
                self.assertEqual(out["links"][0]["url"], "https://car.autohome.com.cn/x9_2026/configuration.html")

    def test_web_fetch_blocks_non_autohome_domains_by_default(self) -> None:
        with self.assertRaises(Exception):
            WebFetchTool().run({"url": "https://example.com/"}, self.ctx)

    def test_registry_preflight_rejects_domain_before_http_request(self) -> None:
        registry = ToolRegistry([WebFetchTool()])
        with patch.object(socket, "getaddrinfo", return_value=[(None, None, None, None, ("93.184.216.34", 0))]):
            with patch.object(urllib.request, "urlopen") as urlopen:
                decision = registry.preflight(
                    ToolCall(name="WebFetch", input={"url": "https://example.com/"}),
                    self.ctx,
                )
        self.assertFalse(decision.can_execute)
        self.assertEqual(decision.reason_code, "WEB_DOMAIN_NOT_ALLOWED")
        urlopen.assert_not_called()

    def test_registry_preflight_allows_public_host_discovered_by_web_search(self) -> None:
        self.ctx.runtime_state["web_search_urls"] = ["https://www.bmw.com/example/source"]
        registry = ToolRegistry([WebFetchTool()])
        with patch.object(socket, "getaddrinfo", return_value=[(None, None, None, None, ("93.184.216.34", 0))]):
            decision = registry.preflight(
                ToolCall(name="WebFetch", input={"url": "https://www.bmw.com/another-official-page"}),
                self.ctx,
            )
        self.assertTrue(decision.can_execute)

    def test_web_fetch_spec_exposes_real_domain_boundary(self) -> None:
        spec = WebFetchTool().spec()
        self.assertIn("autohome.com.cn", spec.description)
        self.assertTrue(any("autohome.com.cn" in item for item in spec.capability.limitations))

    def test_web_fetch_extracts_embedded_config_rows(self) -> None:
        html_doc = r'''
        <html><head><title>参数配置</title></head><body>
        <script>self.__next_f.push([1,"{\"name\":\"制动踏板感模式\",\"data\":[\"平缓/适中/灵敏\",\"平缓/适中/灵敏\"]}"])</script>
        </body></html>
        '''

        class _Resp(io.BytesIO):
            headers = {"Content-Type": "text/html"}

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        with patch.object(socket, "getaddrinfo", return_value=[(None, None, None, None, ("93.184.216.34", 0))]):
            with patch.object(urllib.request, "urlopen", return_value=_Resp(html_doc.encode("utf-8"))):
                out = WebFetchTool().run({"url": "https://car-web-api.autohome.com.cn/config"}, self.ctx).output
                self.assertIn("Extracted structured rows", out["content"])
                self.assertIn("制动踏板感模式", out["content"])
                self.assertEqual(out["structured_rows"][0]["name"], "制动踏板感模式")


class TestWebSearchTool(ToolSystemTests):
    def test_web_search_parses_results(self) -> None:
        html_doc = """
        <a class="result__a" href="https://example.com/">Example</a>
        <a class="result__snippet">Snippet</a>
        """

        class _Resp(io.BytesIO):
            headers = {"Content-Type": "text/html"}

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        with patch.object(urllib.request, "urlopen", return_value=_Resp(html_doc.encode("utf-8"))):
            out = WebSearchTool().run({"query": "example", "num": 1}, self.ctx).output
            self.assertEqual(len(out["results"]), 1)
            self.assertEqual(out["results"][0]["url"], "https://example.com/")
            self.assertIn("https://example.com/", self.ctx.runtime_state["web_search_urls"])

    def test_web_search_falls_back_to_generic_links(self) -> None:
        html_doc = """
        <html><body>
        <a href="/about">skip</a>
        <a href="https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fx9">Example X9</a>
        <span>Useful snippet</span>
        </body></html>
        """

        class _Resp(io.BytesIO):
            headers = {"Content-Type": "text/html"}

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        with patch.object(urllib.request, "urlopen", return_value=_Resp(html_doc.encode("utf-8"))):
            out = WebSearchTool().run({"query": "example", "num": 1}, self.ctx).output
            self.assertEqual(len(out["results"]), 1)
            self.assertEqual(out["results"][0]["url"], "https://example.com/x9")


class TestSubjectsAttributeLookupTool(ToolSystemTests):
    def test_lookup_joins_vehicle_attribute_values(self) -> None:
        rows = [
            {
                "vehicle_id": 2760,
                "vehicle_code": "SU7_MAX",
                "vehicle_name": "小米SU7 Max",
                "attribute_id": 699,
                "attribute_code": "wheelbase",
                "attribute_name": "轴距",
                "attribute_unit": "mm",
                "value_number": 3000,
                "value_text": "3000",
                "unit": "mm",
                "target_type": "vehicle",
            }
        ]
        candidates = [
            {
                "attribute_id": 699,
                "attribute_code": "wheelbase",
                "attribute_name": "轴距",
                "attribute_unit": "mm",
                "covered_vehicle_count": 1,
                "populated_value_count": 1,
            }
        ]
        with patch("src.tool_system.tools.subjects_sql._query", side_effect=[candidates, rows]) as mocked:
            out = SubjectsAttributeLookupTool().run(
                {"entity_keyword": "小米SU7", "attribute_keyword": "轴距"},
                self.ctx,
            ).output

        self.assertEqual(out["row_count"], 1)
        self.assertEqual(out["rows"][0]["vehicle_name"], "小米SU7 Max")
        self.assertEqual(out["rows"][0]["attribute_name"], "轴距")
        args = mocked.call_args.args
        self.assertIn("instance_attribute_value", args[0])
        self.assertEqual(args[1][0], "%小米SU7%")
        self.assertEqual(args[1][2], 699)

    def test_lookup_validates_inputs(self) -> None:
        with self.assertRaises(Exception):
            SubjectsAttributeLookupTool().run({"entity_keyword": "SU7", "attribute_keyword": ""}, self.ctx)

    def test_lookup_supports_all_vehicles_when_entity_keyword_is_omitted(self) -> None:
        with patch("src.tool_system.tools.subjects_sql._query", return_value=[]) as mocked:
            result = ToolRegistry([SubjectsAttributeLookupTool()]).dispatch(
                ToolCall(name="SubjectsAttributeLookup", input={"attribute": "轴距"}),
                self.ctx,
            )
        self.assertFalse(result.is_error)
        self.assertEqual(result.output["entity_keyword"], "*")
        self.assertEqual(mocked.call_args.args[1][0], "%轴%")

    def test_lookup_supports_attribute_value_filter_without_new_tool_layer(self) -> None:
        target_candidates = [
            {
                "attribute_id": 713,
                "attribute_code": "ah_frunk_volume",
                "attribute_name": "前备厢容积(L)",
                "attribute_unit": "L",
                "covered_vehicle_count": 0,
                "populated_value_count": 0,
            }
        ]
        filter_candidates = [
            {
                "attribute_id": 667,
                "attribute_code": "ah_level",
                "attribute_name": "级别",
                "attribute_unit": None,
                "covered_vehicle_count": 3718,
                "populated_value_count": 3718,
            }
        ]
        count_rows = [{"filtered_entity_count": 128}]
        rows: list[dict[str, object]] = []
        with patch(
            "src.tool_system.tools.subjects_sql._query",
            side_effect=[target_candidates, filter_candidates, count_rows, rows],
        ) as mocked:
            result = SubjectsAttributeLookupTool().run(
                {
                    "entity_keyword": "*",
                    "attribute_keyword": "前备箱",
                    "filter_attribute_keyword": "级别",
                    "filter_value_keyword": "MPV",
                },
                self.ctx,
            )

        self.assertFalse(result.is_error)
        self.assertEqual(result.output["filtered_entity_count"], 128)
        self.assertEqual(result.output["filter_attribute_candidates"][0]["attribute_name"], "级别")
        final_sql = mocked.call_args_list[-1].args[0]
        final_params = mocked.call_args_list[-1].args[1]
        self.assertIn("filter_iav", final_sql)
        self.assertIn("filter_ea.id IN", final_sql)
        self.assertIn("%MPV%", final_params)

    def test_lookup_discovers_filter_attribute_from_value_distribution(self) -> None:
        target_candidates = [
            {
                "attribute_id": 713,
                "attribute_code": "ah_frunk_volume",
                "attribute_name": "前备厢容积(L)",
                "attribute_unit": "L",
                "covered_vehicle_count": 0,
                "populated_value_count": 0,
            }
        ]
        discovered_filter_candidates = [
            {
                "attribute_id": 667,
                "attribute_code": "ah_level",
                "attribute_name": "级别",
                "attribute_unit": None,
                "matched_vehicle_count": 128,
                "matched_value_count": 128,
                "sample_value": "中大型MPV",
                "match_score": 51.3,
                "match_reasons": ["value_distribution_match"],
            }
        ]
        count_rows = [{"filtered_entity_count": 128}]
        rows: list[dict[str, object]] = []
        with patch(
            "src.tool_system.tools.subjects_sql._query",
            side_effect=[target_candidates, discovered_filter_candidates, count_rows, rows],
        ) as mocked:
            result = SubjectsAttributeLookupTool().run(
                {
                    "entity_keyword": "*",
                    "attribute_keyword": "前备箱",
                    "filter_value_keyword": "MPV",
                },
                self.ctx,
            )

        self.assertFalse(result.is_error)
        self.assertEqual(result.output["filter_attribute_keyword"], None)
        self.assertEqual(result.output["filter_attribute_candidates"][0]["attribute_name"], "级别")
        self.assertEqual(result.output["filtered_entity_count"], 128)
        value_discovery_sql = mocked.call_args_list[1].args[0]
        self.assertIn("value_text LIKE", value_discovery_sql)

    def test_field_resolver_prefers_front_trunk_over_rear_trunk(self) -> None:
        from src.tool_system.tools.subjects_sql import _field_similarity, _relevant_attribute_candidates

        front_score, front_reasons = _field_similarity("前备箱", "前备厢容积(L)", "ah_field_front")
        rear_score, rear_reasons = _field_similarity("前备箱", "后备厢容积(L)", "ah_field_rear")

        self.assertGreater(front_score, rear_score)
        self.assertIn("direction_match", front_reasons)
        self.assertIn("direction_conflict", rear_reasons)
        relevant = _relevant_attribute_candidates(
            [
                {"attribute_id": 713, "attribute_name": "前备厢容积(L)", "match_score": front_score},
                {"attribute_id": 700, "attribute_name": "前轮距(mm)", "match_score": 39.0},
                {"attribute_id": 714, "attribute_name": "后备厢容积(L)", "match_score": rear_score},
            ],
            limit=10,
        )
        self.assertEqual([item["attribute_id"] for item in relevant], [713])

    def test_lookup_reports_dataset_boundary_for_zero_rows(self) -> None:
        with patch("src.tool_system.tools.subjects_sql._query", return_value=[]):
            result = SubjectsAttributeLookupTool().run(
                {"entity_keyword": "某车型", "attribute_keyword": "减震器阀系"},
                self.ctx,
            )
        self.assertFalse(result.is_error)
        self.assertEqual(result.outcome_status, ToolOutcomeStatus.NO_DATA)
        self.assertEqual(result.reason_code, "VEHICLE_ATTRIBUTE_NO_ROWS")
        self.assertIn("does not establish", result.output["coverage_boundary"])

    def test_lookup_preflight_rejects_unsupported_entity_capability(self) -> None:
        decision = ToolRegistry([SubjectsAttributeLookupTool()]).preflight(
            ToolCall(
                name="SubjectsAttributeLookup",
                input={
                    "entity_keyword": "CDC减震器",
                    "attribute_keyword": "供应商",
                    "entity_type": "component",
                },
            ),
            self.ctx,
        )
        self.assertFalse(decision.can_execute)
        self.assertEqual(decision.reason_code, "CAPABILITY_ENTITY_UNSUPPORTED")
        self.assertIn("data.component.query", decision.alternative_capabilities)

    def test_sql_tool_preflight_fails_closed_without_production_database_config(self) -> None:
        with patch.dict(os.environ, {"APP_ENV": "production"}, clear=True):
            registry = ToolRegistry([SubjectsAttributeLookupTool()])
            decision = registry.preflight(
                ToolCall(
                    name="SubjectsAttributeLookup",
                    input={"entity_keyword": "SU7", "attribute_keyword": "轴距"},
                ),
                self.ctx,
            )
            eligible_names = {spec.name for spec in registry.list_eligible_specs(self.ctx)}
        self.assertFalse(decision.can_execute)
        self.assertEqual(decision.reason_code, "DEPENDENCY_NOT_CONFIGURED")
        self.assertTrue(decision.disable_tool_for_run)
        self.assertNotIn("SubjectsAttributeLookup", eligible_names)


class TestSubjectsDataCatalogSearchTool(ToolSystemTests):
    def test_catalog_reports_scoped_field_coverage(self) -> None:
        rows = [{
            "attribute_id": 1,
            "attribute_code": "front_suspension_type",
            "attribute_name": "前悬架类型",
            "attribute_unit": None,
            "covered_vehicle_count": 70,
            "populated_value_count": 70,
        }]
        with patch("src.tool_system.tools.subjects_sql._query", return_value=rows):
            result = SubjectsDataCatalogSearchTool().run({"query": "前悬架"}, self.ctx)
        self.assertEqual(result.outcome_status, ToolOutcomeStatus.SUCCESS)
        self.assertEqual(result.output["matches"][0]["covered_vehicle_count"], 70)

    def test_catalog_returns_coverage_boundary_instead_of_repeated_sql(self) -> None:
        with patch("src.tool_system.tools.subjects_sql._query", return_value=[]):
            result = SubjectsDataCatalogSearchTool().run({"query": "减震器阀系"}, self.ctx)
        self.assertEqual(result.outcome_status, ToolOutcomeStatus.DATA_COVERAGE_INSUFFICIENT)
        self.assertEqual(result.reason_code, "CATALOG_FIELD_NOT_COVERED")
        self.assertIn("No matching business field", result.output["coverage_boundary"])

    def test_catalog_declared_keyword_alias_is_normalized_before_validation(self) -> None:
        with patch("src.tool_system.tools.subjects_sql._query", return_value=[]):
            result = ToolRegistry([SubjectsDataCatalogSearchTool()]).dispatch(
                ToolCall(name="SubjectsDataCatalogSearch", input={"keyword": "轴距"}),
                self.ctx,
            )
        self.assertEqual(result.output["query"], "轴距")
        normalized = [event for event in self.ctx.audit_events if event.get("event") == "tool_input_normalized"]
        self.assertEqual(normalized[0]["aliases"], {"keyword": "query"})


class TestSubjectsAttributeStatsTool(ToolSystemTests):
    def test_stats_aggregates_numeric_vehicle_attributes(self) -> None:
        candidates = [
            {
                "attribute_id": 700,
                "attribute_code": "front_track",
                "attribute_name": "前轮距(mm)",
                "attribute_unit": "mm",
                "covered_vehicle_count": 2,
                "populated_value_count": 2,
            }
        ]
        stats = [
            {
                "attribute_id": 700,
                "attribute_code": "front_track",
                "attribute_name": "前轮距(mm)",
                "attribute_unit": "mm",
                "vehicle_count": 2,
                "numeric_value_count": 2,
                "min_value": 1600,
                "max_value": 1800,
                "avg_value": 1700,
                "stddev_pop": 100,
            }
        ]
        samples = [
            {
                "vehicle_id": 1,
                "vehicle_code": "V1",
                "vehicle_name": "车型1",
                "attribute_id": 700,
                "attribute_code": "front_track",
                "attribute_name": "前轮距(mm)",
                "unit": "mm",
                "value_number": 1600,
            }
        ]
        with patch("src.tool_system.tools.subjects_sql._query", side_effect=[candidates, stats, samples]) as mocked:
            result = SubjectsAttributeStatsTool().run({"attribute_keywords": ["前轮距"]}, self.ctx)

        self.assertEqual(result.outcome_status, ToolOutcomeStatus.SUCCESS)
        self.assertEqual(result.output["populated_numeric_value_count"], 2)
        aggregate_sql = mocked.call_args_list[1].args[0]
        self.assertIn("ea.code AS attribute_code", aggregate_sql)
        self.assertIn("vi.status = 'active'", aggregate_sql)

    def test_stats_validates_keywords(self) -> None:
        decision = ToolRegistry([SubjectsAttributeStatsTool()]).preflight(
            ToolCall(name="SubjectsAttributeStats", input={}),
            self.ctx,
        )

        self.assertFalse(decision.can_execute)
        self.assertEqual(decision.reason_code, "VEHICLE_STATS_KEYWORD_MISSING")


class TestSubjectsSqlQueryPolicy(ToolSystemTests):
    def test_unknown_table_is_rejected_before_database_execution(self) -> None:
        registry = ToolRegistry([SubjectsSqlQueryTool()])
        with patch("src.tool_system.tools.subjects_sql._query") as query:
            result = registry.dispatch(
                ToolCall(name="SubjectsSqlQuery", input={"query": "SELECT * FROM vehicle"}),
                self.ctx,
            )
        self.assertTrue(result.is_error)
        self.assertEqual(result.reason_code, "SQL_POLICY_REJECTED")
        self.assertIn("vehicle", str(result.output))
        query.assert_not_called()

    def test_cte_aliases_are_not_treated_as_physical_tables(self) -> None:
        decision = ToolRegistry([SubjectsSqlQueryTool()]).preflight(
            ToolCall(
                name="SubjectsSqlQuery",
                input={
                    "query": (
                        "WITH wheelbase_data AS ("
                        "SELECT attribute_id FROM instance_attribute_value"
                        "), merged AS (SELECT attribute_id FROM wheelbase_data) "
                        "SELECT * FROM merged"
                    )
                },
            ),
            self.ctx,
        )
        self.assertTrue(decision.can_execute, decision.message)


class TestSleepTool(ToolSystemTests):
    def test_sleep_short(self) -> None:
        start = time.time()
        SleepTool().run({"seconds": 0.01}, self.ctx)
        self.assertGreaterEqual(time.time() - start, 0.0)


class TestKnowledgeTools(ToolSystemTests):
    def test_platform_knowledge_search_uses_platform_api(self) -> None:
        payload = {
            "provider": "platform",
            "results": [{
                "chunk_id": "chunk-1",
                "document_id": "doc-1",
                "dataset_id": "manual-corpus",
                "title": "ET5 用户手册",
                "source": "https://example.com/manual.pdf",
                "excerpt": "遥控泊车说明",
                "score": 0.9,
            }],
        }

        class _Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        with patch.dict(os.environ, {"RAG_PROVIDER": "platform", "RAG_PLATFORM_BASE_URL": "http://platform.local"}, clear=False):
            with patch.object(urllib.request, "urlopen", return_value=_Resp(json.dumps(payload).encode("utf-8"))) as mocked:
                result = KnowledgeSearchTool().run({"query": "遥控泊车", "top_k": 3}, self.ctx)

        self.assertFalse(result.is_error)
        self.assertEqual(result.output["provider"], "platform")
        self.assertEqual(result.output["results"][0]["chunk_id"], "chunk-1")
        request = mocked.call_args.args[0]
        self.assertEqual(request.full_url, "http://platform.local/api/rag/search")

    def test_platform_knowledge_search_rejects_topical_but_wrong_entity_results(self) -> None:
        payload = {
            "provider": "platform",
            "results": [{
                "chunk_id": "chunk-m5",
                "document_id": "doc-m5",
                "title": "问界 / M5 / 用户手册",
                "source": "https://example.com/m5.pdf",
                "excerpt": "前悬架采用双叉臂，后悬架采用多连杆。",
                "metadata": {"brand": "问界", "model": "M5"},
                "score": 0.03,
            }],
        }

        class _Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        with patch.dict(os.environ, {"RAG_PROVIDER": "platform", "RAG_PLATFORM_BASE_URL": "http://platform.local"}, clear=False):
            with patch.object(urllib.request, "urlopen", return_value=_Resp(json.dumps(payload).encode("utf-8"))):
                result = KnowledgeSearchTool().run(
                    {"query": "BMW G15 8系 前悬架 双叉臂"},
                    self.ctx,
                )

        self.assertEqual(result.outcome_status, ToolOutcomeStatus.DATA_COVERAGE_INSUFFICIENT)
        self.assertEqual(result.reason_code, "KNOWLEDGE_ENTITY_COVERAGE_INSUFFICIENT")
        self.assertEqual(result.output["results"], [])
        self.assertIn("bmw", result.output["entity_anchors"])
        self.assertIn("8系", result.output["entity_anchors"])

    def test_knowledge_search_reports_missing_configuration(self) -> None:
        env = {
            "RAG_PROVIDER": "ragflow",
            "RAGFLOW_BASE_URL": "",
            "RAGFLOW_API_KEY": "",
            "RAGFLOW_DATASET_ID": "",
            "RAGFLOW_DATASET_IDS": "",
        }
        with patch.dict(os.environ, env, clear=False):
            result = KnowledgeSearchTool().run({"query": "小鹏X9制动器"}, self.ctx)
        self.assertTrue(result.is_error)
        self.assertEqual(result.output["provider"], "ragflow")
        self.assertIn("RAGFLOW_BASE_URL", result.output["missing"])

    def test_knowledge_search_normalizes_ragflow_hits(self) -> None:
        payload = {
            "data": {
                "chunks": [
                    {
                        "id": "chunk-1",
                        "document_id": "doc-1",
                        "dataset_id": "dataset-1",
                        "document_name": "X9制动系统报告.pdf",
                        "content": "小鹏X9采用线控制动系统，支持舒适和运动制动脚感。",
                        "page": "12",
                        "score": "0.91",
                        "metadata": {"source": "minio://reports/x9-brake.pdf"},
                    }
                ]
            }
        }

        class _Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        env = {
            "RAG_PROVIDER": "ragflow",
            "RAGFLOW_BASE_URL": "http://ragflow.local",
            "RAGFLOW_API_KEY": "test-key",
            "RAGFLOW_DATASET_ID": "dataset-1",
        }
        with patch.dict(os.environ, env, clear=False):
            with patch.object(urllib.request, "urlopen", return_value=_Resp(json.dumps(payload).encode("utf-8"))) as mocked:
                result = KnowledgeSearchTool().run(
                    {
                        "query": "小鹏X9制动器",
                        "top_k": 3,
                        "document_ids": ["doc-1"],
                        "metadata_condition": {
                            "logic": "and",
                            "conditions": [
                                {"name": "vehicle", "comparison_operator": "=", "value": "小鹏X9"}
                            ],
                        },
                        "keyword": True,
                    },
                    self.ctx,
                )

        self.assertFalse(result.is_error)
        self.assertEqual(result.output["result_count"], 1)
        hit = result.output["results"][0]
        self.assertEqual(hit["chunk_id"], "chunk-1")
        self.assertEqual(hit["document_id"], "doc-1")
        self.assertEqual(hit["dataset_id"], "dataset-1")
        self.assertEqual(hit["title"], "X9制动系统报告.pdf")
        self.assertEqual(hit["source"], "minio://reports/x9-brake.pdf")
        self.assertEqual(hit["page"], 12)
        self.assertEqual(hit["score"], 0.91)
        self.assertIn("线控制动", hit["excerpt"])
        request = mocked.call_args.args[0]
        self.assertEqual(request.full_url, "http://ragflow.local/api/v1/retrieval")
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(body["dataset_ids"], ["dataset-1"])
        self.assertEqual(body["document_ids"], ["doc-1"])
        self.assertTrue(body["keyword"])
        self.assertEqual(body["metadata_condition"]["conditions"][0]["value"], "小鹏X9")

    def test_knowledge_fetch_spec_accepts_dataset_document_and_chunk(self) -> None:
        schema = KnowledgeFetchTool().spec().input_schema
        self.assertIn("dataset_id", schema["properties"])
        self.assertIn("chunk_id", schema["properties"])
        self.assertIn("document_id", schema["properties"])

    def test_knowledge_fetch_uses_official_chunk_endpoint(self) -> None:
        payload = {
            "code": 0,
            "data": {
                "chunks": [
                    {
                        "id": "chunk-1",
                        "document_id": "doc-1",
                        "content": "完整 chunk 内容",
                        "docnm_kwd": "X9制动系统报告.pdf",
                    }
                ],
                "doc": {"dataset_id": "dataset-1", "id": "doc-1", "name": "X9制动系统报告.pdf"},
            },
        }

        class _Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        env = {
            "RAG_PROVIDER": "ragflow",
            "RAGFLOW_BASE_URL": "http://ragflow.local",
            "RAGFLOW_API_KEY": "test-key",
            "RAGFLOW_DATASET_ID": "dataset-1",
        }
        with patch.dict(os.environ, env, clear=False):
            with patch.object(urllib.request, "urlopen", return_value=_Resp(json.dumps(payload).encode("utf-8"))) as mocked:
                result = KnowledgeFetchTool().run(
                    {"dataset_id": "dataset-1", "document_id": "doc-1", "chunk_id": "chunk-1"},
                    self.ctx,
                )

        self.assertFalse(result.is_error)
        hit = result.output["results"][0]
        self.assertEqual(hit["chunk_id"], "chunk-1")
        self.assertEqual(hit["dataset_id"], "dataset-1")
        self.assertEqual(hit["title"], "X9制动系统报告.pdf")
        self.assertEqual(hit["source"], "X9制动系统报告.pdf")
        request = mocked.call_args.args[0]
        self.assertEqual(
            request.full_url,
            "http://ragflow.local/api/v1/datasets/dataset-1/documents/doc-1/chunks?page=1&page_size=5&id=chunk-1",
        )

    def test_default_registry_includes_knowledge_tools(self) -> None:
        registry = build_default_registry(include_user_tools=False)
        names = {spec.name for spec in registry.list_specs()}
        self.assertIn("SubjectsAttributeLookup", names)
        self.assertIn("KnowledgeSearch", names)
        self.assertIn("KnowledgeFetch", names)
        self.assertNotIn("WebSearch", names)


class TestTaskStopTool(ToolSystemTests):
    def test_task_stop(self) -> None:
        def target(stop_event):
            while not stop_event.is_set():
                time.sleep(0.01)

        task = self.ctx.task_manager.start(name="loop", target=target)
        out = TaskStopTool().run({"task_id": task.task_id}, self.ctx).output
        self.assertTrue(out["stopped"])


class TestConfigTool(ToolSystemTests):
    def test_config_get_set_roundtrip(self) -> None:
        from src import config as config_mod

        cfg_path = self.root / "config.json"
        cfg_path.write_text(json.dumps(config_mod.get_default_config()), encoding="utf-8")
        with patch("src.config.get_config_path", return_value=cfg_path):
            get_out = ConfigTool().run({"setting": "default_provider"}, self.ctx).output
            self.assertEqual(get_out["operation"], "get")
            set_out = ConfigTool().run({"setting": "default_provider", "value": "openai"}, self.ctx).output
            self.assertEqual(set_out["operation"], "set")
            self.assertEqual(ConfigTool().run({"setting": "default_provider"}, self.ctx).output["value"], "openai")


class TestMCPTool(ToolSystemTests):
    def test_mcp_calls_client(self) -> None:
        class Client:
            def call_tool(self, tool_name: str, args: dict) -> Any:
                return {"tool": tool_name, "args": args}

            def list_tools(self) -> list[str]:
                return ["x"]

        self.ctx.mcp_clients["srv"] = Client()
        out = MCPTool().run({"server": "srv", "tool": "x", "input": {"a": 1}}, self.ctx).output
        self.assertEqual(out["output"]["args"]["a"], 1)


class TestLSPTool(ToolSystemTests):
    def test_lsp_requires_client(self) -> None:
        out = LSPTool().run({"method": "initialize", "params": {}}, self.ctx)
        self.assertTrue(out.is_error)

    def test_lsp_calls_client(self) -> None:
        class Client:
            def request(self, method: str, params=None) -> Any:
                return {"method": method, "params": params}

        self.ctx.lsp_client = Client()
        out = LSPTool().run({"method": "hover", "params": {"x": 1}}, self.ctx).output
        self.assertEqual(out["response"]["params"]["x"], 1)


class TestSkillTool(ToolSystemTests):
    def test_skill_runs_markdown_skill(self) -> None:
        from src.skills.create import create_skill

        skills_dir = self.root / "skills"
        create_skill(
            directory=skills_dir,
            name="hello",
            description="say hello",
            body="Hello $ARGUMENTS[0]!",
            arguments=["name"],
        )
        with patch.dict(os.environ, {"CLAWD_SKILLS_DIR": str(skills_dir)}):
            out = SkillTool().run({"skill": "hello", "args": "bob"}, self.ctx).output
            self.assertTrue(out["success"])
            self.assertIn("Hello bob!", out["prompt"])
            self.assertEqual(out["loadedFrom"], "user")

    def test_skill_runs_legacy_python_skill(self) -> None:
        skills_dir = self.root / "skills"
        skills_dir.mkdir(parents=True, exist_ok=True)
        (skills_dir / "legacy.py").write_text(
            "def run(input, context):\n    return 'hi ' + input.get('name','world')\n",
            encoding="utf-8",
        )
        with patch.dict(os.environ, {"CLAWD_SKILLS_DIR": str(skills_dir)}):
            out = SkillTool().run({"name": "legacy", "input": {"name": "bob"}}, self.ctx).output
            self.assertEqual(out["output"], "hi bob")


class TestNewParityTools(ToolSystemTests):
    def test_ask_user_question_uses_handler(self) -> None:
        self.ctx.ask_user = lambda questions: {questions[0]["question"]: "Option A"}
        out = AskUserQuestionTool().run(
            {
                "questions": [
                    {
                        "question": "Choose?",
                        "header": "Choice",
                        "options": [
                            {"label": "Option A", "description": "A"},
                            {"label": "Option B", "description": "B"},
                        ],
                    }
                ]
            },
            self.ctx,
        ).output
        self.assertEqual(out["answers"]["Choose?"], "Option A")

    def test_todo_write(self) -> None:
        out = TodoWriteTool().run(
            {"todos": [{"content": "x", "status": "pending", "activeForm": "Doing x"}]},
            self.ctx,
        ).output
        self.assertEqual(out["newTodos"][0]["content"], "x")

    def test_task_tools_roundtrip(self) -> None:
        created = TaskCreateTool().run({"subject": "T1", "description": "D1"}, self.ctx).output
        task_id = created["task"]["id"]
        listed = TaskListTool().run({}, self.ctx).output
        self.assertEqual(len(listed["tasks"]), 1)
        TaskUpdateTool().run({"taskId": task_id, "status": "completed"}, self.ctx)
        got = TaskGetTool().run({"taskId": task_id}, self.ctx).output
        self.assertEqual(got["task"]["status"], "completed")
        task_out = TaskOutputTool().run({"task_id": task_id}, self.ctx).output
        self.assertEqual(task_out["task"]["task_id"], task_id)

    def test_tool_search(self) -> None:
        reg = build_default_registry(include_user_tools=False)
        out = ToolSearchTool(reg).run({"query": "read"}, self.ctx).output
        self.assertIn("Read", out["matches"])

    def test_tool_search_does_not_reveal_ineligible_tools(self) -> None:
        reg = build_default_registry(include_user_tools=False)
        restricted = ToolContext(workspace_root=self.root, allowed_tools=frozenset({"Read", "ToolSearch"}))
        out = ToolSearchTool(reg).run({"query": "write file", "max_results": 20}, restricted).output
        self.assertNotIn("Write", out["matches"])
        self.assertTrue(all(candidate["name"] != "Write" for candidate in out["candidates"]))

    def test_cron_tools_roundtrip(self) -> None:
        created = CronCreateTool().run({"cron": "*/5 * * * *", "prompt": "ping"}, self.ctx).output
        cron_id = created["id"]
        listed = CronListTool().run({}, self.ctx).output
        self.assertEqual(len(listed["jobs"]), 1)
        deleted = CronDeleteTool().run({"id": cron_id}, self.ctx).output
        self.assertTrue(deleted["success"])

    def test_structured_output(self) -> None:
        out = StructuredOutputTool().run({"ok": True}, self.ctx).output
        self.assertTrue(out["structured_output"]["ok"])

    def test_mcp_resource_tools(self) -> None:
        class Client:
            def list_resources(self):
                return [{"uri": "x://1", "name": "r1", "mimeType": "text/plain"}]

            def read_resource(self, uri: str):
                return {"contents": [{"uri": uri, "text": "hello"}]}

        self.ctx.mcp_clients["srv"] = Client()
        listed = ListMcpResourcesTool().run({"server": "srv"}, self.ctx).output
        self.assertEqual(listed[0]["uri"], "x://1")
        read = ReadMcpResourceTool().run({"server": "srv", "uri": "x://1"}, self.ctx).output
        self.assertEqual(read["contents"][0]["text"], "hello")


class TestTaskOutputContract(ToolSystemTests):
    def test_extracts_exact_chinese_ppt_slide_count(self) -> None:
        contract = OutputContract.from_user_request("做一个悬架调研，共6页PPT")
        self.assertTrue(contract.required)
        self.assertEqual(contract.required_artifacts[0].artifact_type, "pptx")
        self.assertEqual(contract.required_artifacts[0].exact_count, 6)

    def test_validates_real_pptx_structure_and_slide_count(self) -> None:
        state = TaskRequirementState.from_user_request("生成2页PPT")
        artifact = self.root / "two-slides.pptx"
        with zipfile.ZipFile(artifact, "w") as archive:
            archive.writestr("ppt/slides/slide1.xml", "<p:sld/>")
            archive.writestr("ppt/slides/slide2.xml", "<p:sld/>")
        state.update_from_tool_result(
            "AutoPptxGenerate",
            ToolResult(name="AutoPptxGenerate", output={"file_path": str(artifact), "slide_count": 999}),
            self.ctx,
        )
        self.assertTrue(state.is_satisfied)
        self.assertEqual(state.status, "satisfied")

    def test_does_not_trust_reported_slide_count(self) -> None:
        state = TaskRequirementState.from_user_request("生成2页PPT")
        artifact = self.root / "one-slide.pptx"
        with zipfile.ZipFile(artifact, "w") as archive:
            archive.writestr("ppt/slides/slide1.xml", "<p:sld/>")
        state.update_from_tool_result(
            "AutoPptxGenerate",
            ToolResult(name="AutoPptxGenerate", output={"file_path": str(artifact), "slide_count": 2}),
            self.ctx,
        )
        self.assertFalse(state.is_satisfied)
        requirement = state.requirements["artifact:pptx"]
        self.assertIn("expected 2", requirement.blocking_reason or "")

    def test_ppt_preflight_rejects_call_that_cannot_satisfy_user_count(self) -> None:
        state = TaskRequirementState.from_user_request("生成6页PPT")
        self.ctx.runtime_state["output_contract"] = state.output_contract
        decision = ToolRegistry([AutoPptxGenerateTool()]).preflight(
            ToolCall(
                name="AutoPptxGenerate",
                input={
                    "requested_slide_count": 5,
                    "deck_title": "Wrong count",
                    "file_name": "wrong.pptx",
                    "slides": [
                        {"title": f"Slide {index}", "conclusion": "Conclusion"}
                        for index in range(5)
                    ],
                },
            ),
            self.ctx,
        )
        self.assertFalse(decision.can_execute)
        self.assertEqual(decision.reason_code, "PPTX_OUTPUT_CONTRACT_MISMATCH")
        self.assertFalse((self.root / "wrong.pptx").exists())

    def test_ppt_contract_requires_explicit_per_slide_tables_and_source_footers(self) -> None:
        state = TaskRequirementState.from_user_request("生成2页PPT，每页加入对比表，并在页脚列出该页来源")
        artifact = state.output_contract.required_artifacts[0]
        self.assertTrue(artifact.require_table_per_slide)
        self.assertTrue(artifact.require_source_per_slide)
        self.ctx.runtime_state["output_contract"] = state.output_contract
        decision = ToolRegistry([AutoPptxGenerateTool()]).preflight(
            ToolCall(
                name="AutoPptxGenerate",
                input={
                    "requested_slide_count": 2,
                    "deck_title": "Contract check",
                    "slides": [
                        {
                            "title": f"Slide {index}",
                            "conclusion": "Conclusion",
                            "table": {"columns": ["A"], "rows": [["B"]]},
                        }
                        for index in range(2)
                    ],
                },
            ),
            self.ctx,
        )
        self.assertFalse(decision.can_execute)
        self.assertEqual(decision.reason_code, "PPTX_SOURCE_FOOTER_REQUIRED")

    def test_ppt_schema_exposes_runtime_content_limits(self) -> None:
        schema = AutoPptxGenerateTool().spec().input_schema
        slide_schema = schema["properties"]["slides"]["items"]
        self.assertEqual(slide_schema["properties"]["key_points"]["maxItems"], 5)
        self.assertEqual(slide_schema["properties"]["table"]["properties"]["rows"]["maxItems"], 8)


class TestRegistryAndHelloWorldTool(ToolSystemTests):
    def test_registry_retries_only_declared_retryable_idempotent_outcome(self) -> None:
        class FlakyTool:
            calls = 0

            def spec(self):
                return ToolSpec(
                    name="Flaky",
                    description="test",
                    input_schema={"type": "object"},
                    execution=ToolExecutionPolicy(
                        retryable_outcomes=(ToolOutcomeStatus.TRANSIENT_FAILURE.value,),
                        max_attempts=3,
                        idempotent=True,
                    ),
                )

            def run(self, tool_input, context):
                self.calls += 1
                if self.calls == 1:
                    return ToolResult(
                        name="Flaky",
                        output={"error": "temporary"},
                        is_error=True,
                        outcome_status=ToolOutcomeStatus.TRANSIENT_FAILURE,
                        reason_code="TEMPORARY",
                        retryable=True,
                    )
                return ToolResult(name="Flaky", output={"ok": True})

        tool = FlakyTool()
        with patch.dict(os.environ, {"CLAWD_TOOL_RETRY_BASE_DELAY_SECONDS": "0"}):
            result = ToolRegistry([tool]).dispatch(ToolCall(name="Flaky", input={}), self.ctx)
        self.assertFalse(result.is_error)
        self.assertEqual(tool.calls, 2)
        self.assertEqual(result.diagnostics["attempt_count"], 2)
        attempts = [event for event in self.ctx.audit_events if event.get("event") == "tool_execution_attempt"]
        self.assertEqual([event["retry_scheduled"] for event in attempts], [True, False])

    def test_registry_never_retries_non_idempotent_tool(self) -> None:
        class SideEffectTool:
            calls = 0

            def spec(self):
                return ToolSpec(
                    name="SideEffect",
                    description="test",
                    input_schema={"type": "object"},
                    execution=ToolExecutionPolicy(
                        side_effect="artifact",
                        retryable_outcomes=(ToolOutcomeStatus.TRANSIENT_FAILURE.value,),
                        max_attempts=3,
                        idempotent=False,
                    ),
                )

            def run(self, tool_input, context):
                self.calls += 1
                return ToolResult(
                    name="SideEffect",
                    output={"error": "temporary"},
                    is_error=True,
                    outcome_status=ToolOutcomeStatus.TRANSIENT_FAILURE,
                    retryable=True,
                )

        tool = SideEffectTool()
        result = ToolRegistry([tool]).dispatch(ToolCall(name="SideEffect", input={}), self.ctx)
        self.assertTrue(result.is_error)
        self.assertEqual(tool.calls, 1)
        self.assertEqual(result.diagnostics["attempt_count"], 1)

    def test_registry_runtime_timeout_does_not_retry_detached_call(self) -> None:
        class SlowTool:
            calls = 0

            def spec(self):
                return ToolSpec(
                    name="SlowBounded",
                    description="test",
                    input_schema={"type": "object"},
                    execution=ToolExecutionPolicy(
                        timeout_s=0.01,
                        retryable_outcomes=(ToolOutcomeStatus.TIMEOUT.value,),
                        max_attempts=3,
                        concurrency_pool="test_timeout",
                        idempotent=True,
                    ),
                )

            def run(self, tool_input, context):
                self.calls += 1
                time.sleep(0.05)
                return ToolResult(name="SlowBounded", output={"ok": True})

        tool = SlowTool()
        result = ToolRegistry([tool]).dispatch(ToolCall(name="SlowBounded", input={}), self.ctx)
        self.assertEqual(result.outcome_status, ToolOutcomeStatus.TIMEOUT)
        self.assertEqual(result.reason_code, "TOOL_EXECUTION_TIMEOUT")
        self.assertFalse(result.retryable)
        self.assertEqual(tool.calls, 1)

    def test_registry_resource_pool_fails_fast_when_saturated(self) -> None:
        started = threading.Event()
        release = threading.Event()

        class PooledTool:
            def spec(self):
                return ToolSpec(
                    name="Pooled",
                    description="test",
                    input_schema={"type": "object"},
                    execution=ToolExecutionPolicy(concurrency_pool="test_saturation"),
                )

            def run(self, tool_input, context):
                started.set()
                release.wait(timeout=1)
                return ToolResult(name="Pooled", output={"ok": True})

        registry = ToolRegistry([PooledTool()])
        first_result: list[ToolResult] = []
        with patch.dict(
            os.environ,
            {
                "CLAWD_TOOL_POOL_TEST_SATURATION_WORKERS": "1",
                "CLAWD_TOOL_POOL_QUEUE_CAPACITY": "0",
            },
        ):
            worker = threading.Thread(
                target=lambda: first_result.append(registry.dispatch(ToolCall(name="Pooled", input={}), self.ctx))
            )
            worker.start()
            self.assertTrue(started.wait(timeout=1))
            second = registry.dispatch(ToolCall(name="Pooled", input={}), self.ctx)
            release.set()
            worker.join(timeout=1)

        self.assertEqual(second.outcome_status, ToolOutcomeStatus.TRANSIENT_FAILURE)
        self.assertEqual(second.reason_code, "RESOURCE_POOL_SATURATED")
        self.assertEqual(len(first_result), 1)
        self.assertFalse(first_result[0].is_error)

    def test_registry_cancellation_returns_without_releasing_running_pool_slot(self) -> None:
        started = threading.Event()
        release = threading.Event()

        class CancellableTool:
            def spec(self):
                return ToolSpec(
                    name="Cancellable",
                    description="test",
                    input_schema={"type": "object"},
                    execution=ToolExecutionPolicy(concurrency_pool="test_cancel", timeout_s=5),
                )

            def run(self, tool_input, context):
                started.set()
                release.wait(timeout=1)
                return ToolResult(name="Cancellable", output={"ok": True})

        context = ToolContext(workspace_root=self.root)
        registry = ToolRegistry([CancellableTool()])
        dispatched: list[ToolResult] = []
        worker = threading.Thread(
            target=lambda: dispatched.append(registry.dispatch(ToolCall(name="Cancellable", input={}), context))
        )
        worker.start()
        self.assertTrue(started.wait(timeout=1))
        context.request_cancel()
        worker.join(timeout=0.5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(dispatched[0].outcome_status, ToolOutcomeStatus.CANCELLED)
        self.assertEqual(dispatched[0].reason_code, "TOOL_EXECUTION_CANCELLED")
        release.set()

    def test_can_load_user_tool_hello_world(self) -> None:
        user_dir = self.root / "tools"
        user_dir.mkdir(parents=True, exist_ok=True)
        (user_dir / "hello.py").write_text(
            "tool_spec = {\n"
            "  'name': 'HelloWorld',\n"
            "  'description': 'hello world tool',\n"
            "  'input_schema': { 'type': 'object', 'properties': { 'name': { 'type': 'string' } } },\n"
            "}\n"
            "def run(tool_input, context):\n"
            "  return { 'message': 'hello ' + tool_input.get('name','world') }\n",
            encoding="utf-8",
        )

        from src.tool_system.loader import load_tools_from_dir

        tools = load_tools_from_dir(user_dir)
        self.assertEqual(len(tools), 1)
        reg = ToolRegistry(tools=tools)
        result = reg.dispatch(ToolCall(name="HelloWorld", input={"name": "alice"}), self.ctx)
        self.assertEqual(result.output["message"], "hello alice")

    def test_registry_enforces_tool_result_size_limit(self) -> None:
        class LargeResultTool:
            def spec(self) -> ToolSpec:
                return ToolSpec(
                    name="LargeResult",
                    description="returns a large payload",
                    input_schema={"type": "object", "additionalProperties": False},
                    max_result_size_chars=500,
                )

            def run(self, tool_input, context) -> ToolResult:
                return ToolResult(name="LargeResult", output={"rows": ["x" * 2000]})

        result = ToolRegistry([LargeResultTool()]).dispatch(ToolCall(name="LargeResult", input={}), self.ctx)

        self.assertFalse(result.is_error)
        self.assertEqual(result.outcome_status, ToolOutcomeStatus.PARTIAL_SUCCESS)
        self.assertEqual(result.reason_code, "RESULT_SIZE_LIMIT")
        self.assertTrue(result.output["truncated"])
        self.assertLessEqual(len(json.dumps(result.output, ensure_ascii=False, separators=(",", ":"))), 500)

    def test_error_result_gets_stable_default_outcome(self) -> None:
        result = ToolResult(name="Failure", output={"error": "failed"}, is_error=True)
        self.assertEqual(result.outcome_status, ToolOutcomeStatus.PERMANENT_FAILURE)


class TestProductionToolContracts(ToolSystemTests):
    def test_all_production_tools_have_non_general_capability_contracts(self) -> None:
        with patch.dict(os.environ, {"APP_ENV": "production", "CLAWD_TOOL_PROFILE": "production"}):
            specs = build_default_registry(include_user_tools=False).list_specs()

        self.assertEqual(len(specs), 15)
        self.assertIn("SubjectsAttributeStats", {spec.name for spec in specs})
        self.assertTrue(all(spec.capability.namespace != "general" for spec in specs))
        self.assertTrue(all(spec.preflight_checks for spec in specs))

    def test_production_registry_can_explicitly_enable_governed_web_search(self) -> None:
        with patch.dict(
            os.environ,
            {
                "APP_ENV": "production",
                "CLAWD_TOOL_PROFILE": "production",
                "CLAWD_ENABLE_WEBSEARCH": "true",
            },
        ):
            specs = build_default_registry(include_user_tools=False).list_specs()

        self.assertEqual(len(specs), 16)
        self.assertIn("WebSearch", {spec.name for spec in specs})

    def test_chart_sql_uses_governed_scope_policy_before_database_execution(self) -> None:
        context = ToolContext(
            workspace_root=self.root,
            data_scope={"vehicle_ids": [1]},
            role_ids=("engineer",),
        )

        result = AutoChartGenerateTool().run(
            {
                "chart_type": "bar",
                "title": "restricted data",
                "sql_query": "SELECT id, vehicle_name FROM vehicle_instance",
                "x": "vehicle_name",
                "y": "id",
            },
            context,
        )

        self.assertTrue(result.is_error)
        self.assertEqual(result.outcome_status, ToolOutcomeStatus.PERMISSION_DENIED)
        self.assertEqual(result.reason_code, "SQL_DATA_SCOPE_REJECTED")
        self.assertIn("governed SQL executor", result.output["error"])


class TestBriefAndAgentTools(ToolSystemTests):
    def test_brief_tool(self) -> None:
        out = BriefTool().run({"text": "abc", "max_chars": 2}, self.ctx).output
        self.assertEqual(out["preview"], "ab…")

    def test_agent_tool_sequences_calls(self) -> None:
        reg = build_default_registry(include_user_tools=False)
        ctx = ToolContext(workspace_root=self.root)
        p = self.root / "x.txt"
        p.write_text("hi", encoding="utf-8")
        call = {"name": "Read", "input": {"file_path": str(p), "limit": 10}}
        out = reg.get("Agent").run({"calls": [call]}, ctx).output  # type: ignore[union-attr]
        self.assertEqual(out["results"][0]["name"], "Read")


class TestTeamTools(ToolSystemTests):
    def test_team_create_roundtrip(self) -> None:
        """Test creating and deleting a team."""
        # Create team
        create_out = TeamCreateTool().run(
            {"team_name": "test-team", "description": "A test team"},
            self.ctx,
        ).output
        self.assertEqual(create_out["team_name"], "test-team")
        self.assertIsNotNone(create_out["lead_agent_id"])
        self.assertEqual(self.ctx.team["team_name"], "test-team")

        # Verify team file was created
        team_file = self.root / ".clawd" / "team.json"
        self.assertTrue(team_file.exists())

        # Delete team
        delete_out = TeamDeleteTool().run({}, self.ctx).output
        self.assertTrue(delete_out["success"])
        self.assertEqual(delete_out["team_name"], "test-team")
        self.assertIsNone(self.ctx.team)

        # Verify team file was deleted
        self.assertFalse(team_file.exists())

    def test_team_delete_no_team(self) -> None:
        """Test deleting when no team exists."""
        out = TeamDeleteTool().run({}, self.ctx).output
        self.assertFalse(out["success"])
        self.assertEqual(out["message"], "No active team")

    def test_team_create_requires_name(self) -> None:
        """Test team name validation."""
        from src.tool_system.errors import ToolInputError

        with self.assertRaises(ToolInputError):
            TeamCreateTool().run({"team_name": ""}, self.ctx)


class TestWorktreeTools(ToolSystemTests):
    def test_worktree_roundtrip(self) -> None:
        """Test entering and exiting a worktree."""
        # Enter worktree
        enter_out = EnterWorktreeTool().run({"name": "test-tree"}, self.ctx).output
        self.assertIn("test-tree", enter_out["worktreePath"])
        self.assertIsNotNone(self.ctx.worktree_root)
        self.assertEqual(self.ctx.cwd, self.ctx.worktree_root)

        # Verify worktree directory exists
        worktree_dir = self.root / ".clawd" / "worktrees" / "test-tree"
        self.assertTrue(worktree_dir.exists())

        # Exit worktree
        exit_out = ExitWorktreeTool().run({}, self.ctx).output
        self.assertIn("Exited worktree", exit_out["message"])
        self.assertIsNone(self.ctx.worktree_root)
        self.assertEqual(self.ctx.cwd, self.root)

    def test_worktree_enter_already_in(self) -> None:
        """Test entering worktree when already in one."""
        from src.tool_system.errors import ToolPermissionError

        EnterWorktreeTool().run({"name": "first"}, self.ctx)
        with self.assertRaises(ToolPermissionError):
            EnterWorktreeTool().run({"name": "second"}, self.ctx)

    def test_worktree_exit_not_in(self) -> None:
        """Test exiting worktree when not in one."""
        from src.tool_system.errors import ToolPermissionError

        with self.assertRaises(ToolPermissionError):
            ExitWorktreeTool().run({}, self.ctx)

    def test_worktree_name_validation(self) -> None:
        """Test worktree name validation."""
        from src.tool_system.errors import ToolInputError

        # Invalid empty name
        with self.assertRaises(ToolInputError):
            EnterWorktreeTool().run({"name": ""}, self.ctx)

        # Invalid characters
        with self.assertRaises(ToolInputError):
            EnterWorktreeTool().run({"name": "invalid name!"}, self.ctx)

        # Too long
        with self.assertRaises(ToolInputError):
            EnterWorktreeTool().run({"name": "a" * 65}, self.ctx)


class TestPlanModeTools(ToolSystemTests):
    def test_plan_mode_roundtrip(self) -> None:
        """Test entering and exiting plan mode."""
        # Enter plan mode
        enter_out = EnterPlanModeTool().run({}, self.ctx).output
        self.assertTrue(self.ctx.plan_mode)
        self.assertIn("Entered plan mode", enter_out["message"])

        # Exit plan mode
        exit_out = ExitPlanModeTool().run({}, self.ctx).output
        self.assertFalse(self.ctx.plan_mode)
        self.assertFalse(exit_out["isAgent"])
        self.assertTrue(exit_out["hasTaskTool"])

    def test_plan_mode_exit_with_plan(self) -> None:
        """Test exiting plan mode with a plan."""
        EnterPlanModeTool().run({}, self.ctx)

        plan_content = "# My Plan\n\n- Do something\n- Do something else"
        exit_out = ExitPlanModeTool().run({"plan": plan_content}, self.ctx).output

        self.assertEqual(exit_out["plan"], plan_content)
        self.assertIsNotNone(exit_out["filePath"])

        # Verify plan file was created
        plan_file = self.root / ".clawd" / "plan.md"
        self.assertTrue(plan_file.exists())
        self.assertEqual(plan_file.read_text(encoding="utf-8"), plan_content)

    def test_plan_mode_exit_with_custom_path(self) -> None:
        """Test exiting plan mode with custom plan file path."""
        EnterPlanModeTool().run({}, self.ctx)

        custom_path = self.root / "my-plan.md"
        plan_content = "# Custom Plan"
        exit_out = ExitPlanModeTool().run(
            {"plan": plan_content, "planFilePath": str(custom_path)},
            self.ctx,
        ).output

        self.assertEqual(exit_out["filePath"], str(custom_path))
        self.assertTrue(custom_path.exists())

    def test_plan_mode_exit_not_in_mode(self) -> None:
        """Test exiting plan mode when not in it."""
        from src.tool_system.errors import ToolPermissionError

        with self.assertRaises(ToolPermissionError):
            ExitPlanModeTool().run({}, self.ctx)

    def test_plan_mode_plan_validation(self) -> None:
        """Test plan input validation."""
        from src.tool_system.errors import ToolInputError

        EnterPlanModeTool().run({}, self.ctx)

        # Plan must be string
        with self.assertRaises(ToolInputError):
            ExitPlanModeTool().run({"plan": 123}, self.ctx)

        # Plan file path must be string
        with self.assertRaises(ToolInputError):
            ExitPlanModeTool().run({"plan": "x", "planFilePath": 123}, self.ctx)


if __name__ == "__main__":
    unittest.main()
