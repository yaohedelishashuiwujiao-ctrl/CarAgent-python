"""Test agent loop with mocked provider to verify tool invocation."""

import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path
import re
import tempfile
import threading
import time
import zipfile

from src.agent.conversation import Conversation
from src.providers.base import ChatResponse
from src.tool_system.defaults import build_default_registry
from src.tool_system.context import ToolContext
from src.tool_system.agent_loop import AgentRunCancelled, _format_citation_list, run_agent_loop, AgentLoopResult
from src.tool_system.protocol import ToolOutcomeStatus, ToolResult
from src.tool_system.preflight import PreflightDecision
from src.tool_system.registry import ToolCapability, ToolExecutionPolicy, ToolSpec, ToolRegistry
from src.tool_system.tools.auto_visuals import AutoPptxGenerateTool
from src.tool_system.tools.tool_search import ToolSearchTool
from src.tool_system.tools.todo_write import TodoWriteTool


class CoverageInsufficientKnowledgeTool:
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="KnowledgeSearch",
            description="test coverage boundary",
            input_schema={
                "type": "object",
                "properties": {"query": {"type": "string"}, "top_k": {"type": "integer"}},
                "required": ["query"],
            },
            is_read_only=True,
            capability=ToolCapability(namespace="knowledge.search"),
            execution=ToolExecutionPolicy(supports_parallel=True, idempotent=True),
        )

    def run(self, tool_input: dict, context: ToolContext) -> ToolResult:
        return ToolResult(
            name="KnowledgeSearch",
            output={"query": tool_input.get("query"), "results": [], "coverage_boundary": "test"},
            outcome_status=ToolOutcomeStatus.DATA_COVERAGE_INSUFFICIENT,
            reason_code="KNOWLEDGE_ENTITY_COVERAGE_INSUFFICIENT",
        )


class TestAgentLoop(unittest.TestCase):
    """Test agent loop logic."""

    def setUp(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name)
        self.registry = build_default_registry()
        self.context = ToolContext(workspace_root=self.workspace)

    def tearDown(self):
        """Clean up test fixtures."""
        self.temp_dir.cleanup()

    def test_agent_loop_calls_tool(self):
        """Test agent loop correctly dispatches a tool call from mocked LLM."""
        conversation = Conversation()
        conversation.add_user_message("Create a file hello.py with content print('hello world')")

        # Mock provider
        mock_provider = MagicMock()
        mock_provider.chat_stream_response.side_effect = NotImplementedError()

        # First response: tool use Write
        mock_tool_use = {
            "id": "toolu_123",
            "name": "Write",
            "input": {
                "file_path": str(self.workspace / "hello.py"),
                "content": "print('hello world')"
            }
        }
        mock_response1 = ChatResponse(
            content="I will create the file.",
            model="test-model",
            usage={"input_tokens": 10, "output_tokens": 20},
            finish_reason="tool_use",
            tool_uses=[mock_tool_use],
        )

        # Second response: final text after tool result
        mock_response2 = ChatResponse(
            content="File created successfully!",
            model="test-model",
            usage={"input_tokens": 30, "output_tokens": 10},
            finish_reason="stop",
            tool_uses=None,
        )

        mock_provider.chat.side_effect = [mock_response1, mock_response2]

        result = run_agent_loop(
            conversation=conversation,
            provider=mock_provider,
            tool_registry=self.registry,
            tool_context=self.context,
            verbose=False,
        )

        # Verify final response
        self.assertIsInstance(result, AgentLoopResult)
        self.assertEqual(result.response_text, "File created successfully!")

        # Verify provider was called twice
        self.assertEqual(mock_provider.chat.call_count, 2)

        # Verify file was created
        hello_py = self.workspace / "hello.py"
        self.assertTrue(hello_py.exists())
        self.assertEqual(hello_py.read_text(), "print('hello world')")

        # Non-Anthropic providers must receive the stable policy and live state
        # on every turn, not only on the first request.
        for provider_call in mock_provider.chat.call_args_list:
            messages = provider_call.args[0]
            self.assertEqual(messages[0]["role"], "system")
            self.assertIn("Runtime run-state ledger", messages[0]["content"])

    def test_agent_loop_returns_route_decision_metadata(self):
        conversation = Conversation()
        conversation.add_user_message("解释一下底盘平台化是什么意思")

        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.return_value = ChatResponse(
            content="底盘平台化是复用架构和接口。",
            model="test-model",
            usage={"input_tokens": 8, "output_tokens": 6},
            finish_reason="stop",
            tool_uses=None,
        )

        result = run_agent_loop(
            conversation=conversation,
            provider=provider,
            tool_registry=ToolRegistry([]),
            tool_context=self.context,
            verbose=False,
        )

        self.assertEqual(result.model_tier, "cheap")
        self.assertEqual(result.budget_class, "lookup")
        self.assertIsInstance(result.route_decision, dict)
        self.assertEqual(result.route_decision["model_tier"], "cheap")

    def test_artifact_route_generates_limited_pptx_when_research_coverage_is_insufficient(self):
        conversation = Conversation()
        conversation.add_user_message(
            "请生成6页PPT，不增加封面页或目录页：\n"
            "1. 8系—前悬架\n"
            "2. 8系—后悬架\n"
            "3. 9系 MPV—前悬架\n"
            "4. 9系 MPV—后悬架\n"
            "5. 东南亚 MPV—前悬架\n"
            "6. 东南亚 MPV—后悬架"
        )
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.return_value = ChatResponse(
            content="先检索资料。",
            model="test-model",
            usage={"input_tokens": 20, "output_tokens": 8},
            finish_reason="tool_use",
            tool_uses=[
                {"id": f"k{i}", "name": "KnowledgeSearch", "input": {"query": f"suspension {i}", "top_k": 5}}
                for i in range(4)
            ],
        )
        events = []

        result = run_agent_loop(
            conversation=conversation,
            provider=provider,
            tool_registry=ToolRegistry([CoverageInsufficientKnowledgeTool(), AutoPptxGenerateTool()]),
            tool_context=self.context,
            verbose=False,
            on_event=events.append,
        )

        self.assertEqual(provider.chat.call_count, 1)
        self.assertEqual(result.output_contract_status, "satisfied")
        self.assertEqual(result.termination_reason, "coverage_limited_artifact_generated")
        pptx_events = [event for event in events if event.tool_name == "AutoPptxGenerate" and event.kind == "tool_result"]
        self.assertEqual(len(pptx_events), 1)
        artifact_path = Path(pptx_events[0].tool_output["file_path"])
        self.assertTrue(artifact_path.exists())
        with zipfile.ZipFile(artifact_path) as archive:
            slide_count = sum(
                1
                for name in archive.namelist()
                if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)
            )
        self.assertEqual(slide_count, 6)

    def test_agent_loop_applies_model_tier_override(self):
        conversation = Conversation()
        conversation.add_user_message("解释一下底盘平台化是什么意思")

        provider = MagicMock()
        provider.model = "default-model"
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.return_value = ChatResponse(
            content="底盘平台化是复用架构和接口。",
            model="cheap-model",
            usage={},
            finish_reason="stop",
            tool_uses=None,
        )

        with patch.dict("os.environ", {"CLAWD_MODEL_TIER_CHEAP_MODEL": "cheap-model"}):
            result = run_agent_loop(
                conversation=conversation,
                provider=provider,
                tool_registry=ToolRegistry([]),
                tool_context=self.context,
                verbose=False,
            )

        self.assertEqual(provider.chat.call_args.kwargs["model"], "cheap-model")
        self.assertEqual(result.model_routing["model_override"], "cheap-model")
        self.assertEqual(result.model_routing["provider_default_model"], "default-model")

    def test_model_plan_tool_hints_narrow_next_candidate_set(self):
        conversation = Conversation()
        conversation.add_user_message("Perform a multi-step custom task")
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.side_effect = [
            ChatResponse(
                content="I will maintain a plan.",
                model="test-model",
                usage={},
                finish_reason="tool_use",
                tool_uses=[{
                    "id": "plan1",
                    "name": "TodoWrite",
                    "input": {
                        "todos": [{
                            "content": "Look up the required fact",
                            "status": "in_progress",
                            "activeForm": "Looking up the fact",
                            "expectedOutcome": "One verified value",
                            "toolHints": ["Lookup"],
                        }]
                    },
                }],
            ),
            ChatResponse(
                content="Looking up the fact.",
                model="test-model",
                usage={},
                finish_reason="tool_use",
                tool_uses=[{"id": "lookup1", "name": "Lookup", "input": {}}],
            ),
            ChatResponse(
                content="Marking the verified step complete.",
                model="test-model",
                usage={},
                finish_reason="tool_use",
                tool_uses=[{
                    "id": "plan2",
                    "name": "TodoWrite",
                    "input": {"todos": [{
                        "content": "Look up the required fact",
                        "status": "completed",
                        "activeForm": "Looking up the fact",
                        "expectedOutcome": "One verified value",
                        "toolHints": ["Lookup"],
                    }]},
                }],
            ),
            ChatResponse(content="Done", model="test-model", usage={}, finish_reason="stop", tool_uses=None),
        ]

        class NamedTool:
            def __init__(self, name):
                self.name = name

            def spec(self):
                return ToolSpec(name=self.name, description=self.name, input_schema={"type": "object"})

            def run(self, tool_input, context):
                return ToolResult(name=self.name, output={"ok": True})

        run_agent_loop(
            conversation=conversation,
            provider=provider,
            tool_registry=ToolRegistry([TodoWriteTool(), NamedTool("Lookup"), NamedTool("Unrelated")]),
            tool_context=self.context,
        )

        second_tools = {item["name"] for item in provider.chat.call_args_list[1].kwargs["tools"]}
        self.assertIn("Lookup", second_tools)
        self.assertIn("TodoWrite", second_tools)
        self.assertNotIn("Unrelated", second_tools)

    def test_premature_final_is_rejected_until_model_owned_plan_is_complete(self):
        conversation = Conversation()
        conversation.add_user_message("汇总全部待办事项")
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        in_progress = {
            "content": "Analyze the governed data",
            "status": "in_progress",
            "activeForm": "Analyzing governed data",
        }
        completed = {**in_progress, "status": "completed"}
        provider.chat.side_effect = [
            ChatResponse(
                content="Planning",
                model="test-model",
                usage={},
                finish_reason="tool_use",
                tool_uses=[{"id": "plan1", "name": "TodoWrite", "input": {"todos": [in_progress]}}],
            ),
            ChatResponse(content="Premature answer", model="test-model", usage={}, finish_reason="stop", tool_uses=None),
            ChatResponse(
                content="Completing plan",
                model="test-model",
                usage={},
                finish_reason="tool_use",
                tool_uses=[{"id": "plan2", "name": "TodoWrite", "input": {"todos": [completed]}}],
            ),
            ChatResponse(content="Verified answer", model="test-model", usage={}, finish_reason="stop", tool_uses=None),
        ]

        result = run_agent_loop(
            conversation=conversation,
            provider=provider,
            tool_registry=ToolRegistry([TodoWriteTool()]),
            tool_context=self.context,
        )

        self.assertEqual(provider.chat.call_count, 4)
        self.assertEqual(result.response_text, "Verified answer")
        self.assertEqual(result.task_contract_status, "satisfied")
        plan_requirement = next(item for item in result.requirements if item["id"] == "plan:completion")
        self.assertEqual(plan_requirement["status"], "satisfied")

    def test_correctable_sql_scope_rejection_does_not_disable_sql_tool(self):
        conversation = Conversation()
        conversation.add_user_message("查询小米SU7轴距")
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.side_effect = [
            ChatResponse(
                content="Querying",
                model="test-model",
                usage={},
                finish_reason="tool_use",
                tool_uses=[{"id": "bad", "name": "SubjectsSqlQuery", "input": {"query": "bad scope"}}],
            ),
            ChatResponse(
                content="Correcting scope",
                model="test-model",
                usage={},
                finish_reason="tool_use",
                tool_uses=[{"id": "good", "name": "SubjectsSqlQuery", "input": {"query": "active scope"}}],
            ),
            ChatResponse(content="轴距为3000mm。", model="test-model", usage={}, finish_reason="stop", tool_uses=None),
        ]

        class ScopeAwareSql:
            def spec(self):
                return ToolSpec(
                    name="SubjectsSqlQuery",
                    description="SQL",
                    input_schema={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
                    is_read_only=True,
                )

            def run(self, tool_input, context):
                if tool_input["query"] == "bad scope":
                    return ToolResult(
                        name="SubjectsSqlQuery",
                        output={"error": "active scope required"},
                        is_error=True,
                        outcome_status=ToolOutcomeStatus.PERMISSION_DENIED,
                        reason_code="SQL_DATA_SCOPE_REJECTED",
                    )
                return ToolResult(
                    name="SubjectsSqlQuery",
                    output={"query": "active scope", "row_count": 1, "rows": [{"wheelbase_mm": 3000}]},
                )

        result = run_agent_loop(
            conversation=conversation,
            provider=provider,
            tool_registry=ToolRegistry([ScopeAwareSql()]),
            tool_context=self.context,
        )

        self.assertIn("轴距为3000mm。", result.response_text)
        second_tools = {item["name"] for item in provider.chat.call_args_list[1].kwargs["tools"]}
        self.assertIn("SubjectsSqlQuery", second_tools)

    def test_unmet_output_contract_uses_provider_specific_tool_choice(self):
        conversation = Conversation()
        conversation.add_user_message("生成2页PPT")
        artifact = self.workspace / "forced-result.pptx"

        class ChoiceProvider:
            def __init__(self):
                self.calls = []
                self.responses = [
                    ChatResponse(content="文字版完成", model="test", usage={}, finish_reason="stop", tool_uses=None),
                    ChatResponse(
                        content="Generating",
                        model="test",
                        usage={},
                        finish_reason="tool_use",
                        tool_uses=[{"id": "ppt", "name": "PptRenderer", "input": {"slides": 2}}],
                    ),
                    ChatResponse(content="PPT完成", model="test", usage={}, finish_reason="stop", tool_uses=None),
                ]

            def format_tool_choice(self, mode, tool_name=None):
                return {"mode": mode, "name": tool_name}

            def chat_stream_response(self, *args, **kwargs):
                raise NotImplementedError

            def chat(self, messages, **kwargs):
                self.calls.append((messages, kwargs))
                return self.responses.pop(0)

        class PptRenderer:
            def spec(self):
                return ToolSpec(
                    name="PptRenderer",
                    description="Render pptx",
                    input_schema={
                        "type": "object",
                        "properties": {"slides": {"type": "integer"}},
                        "required": ["slides"],
                    },
                    capability=ToolCapability(namespace="artifact.pptx", output_modes=("pptx",)),
                )

            def run(self, tool_input, context):
                with zipfile.ZipFile(artifact, "w") as archive:
                    for index in range(1, tool_input["slides"] + 1):
                        archive.writestr(f"ppt/slides/slide{index}.xml", "<p:sld/>")
                return ToolResult(name="PptRenderer", output={"file_path": str(artifact), "slide_count": 2})

        provider = ChoiceProvider()
        result = run_agent_loop(
            conversation=conversation,
            provider=provider,
            tool_registry=ToolRegistry([PptRenderer()]),
            tool_context=self.context,
            max_turns=4,
        )

        self.assertEqual(provider.calls[1][1]["tool_choice"], {"mode": "specific", "name": "PptRenderer"})
        self.assertEqual(result.output_contract_status, "satisfied")
        self.assertTrue(artifact.exists())

    def test_artifact_task_requires_model_owned_plan_before_external_tool(self):
        conversation = Conversation()
        conversation.add_user_message("生成2页PPT")
        artifact = self.workspace / "planned-result.pptx"

        class ChoiceProvider:
            def __init__(self):
                self.calls = []
                self.responses = [
                    ChatResponse(
                        content="先规划",
                        model="test",
                        usage={},
                        finish_reason="tool_use",
                        tool_uses=[{
                            "id": "plan",
                            "name": "TodoWrite",
                            "input": {"todos": [{
                                "content": "生成PPT",
                                "status": "in_progress",
                                "activeForm": "正在生成PPT",
                                "expectedOutcome": "2页pptx文件",
                                "toolHints": ["PptRenderer"],
                            }]},
                        }],
                    ),
                    ChatResponse(
                        content="生成中",
                        model="test",
                        usage={},
                        finish_reason="tool_use",
                        tool_uses=[{"id": "ppt", "name": "PptRenderer", "input": {"slides": 2}}],
                    ),
                    ChatResponse(
                        content="更新完成状态",
                        model="test",
                        usage={},
                        finish_reason="tool_use",
                        tool_uses=[{
                            "id": "plan-done",
                            "name": "TodoWrite",
                            "input": {"todos": [{
                                "content": "生成PPT",
                                "status": "completed",
                                "activeForm": "正在生成PPT",
                                "expectedOutcome": "2页pptx文件",
                                "toolHints": ["PptRenderer"],
                            }]},
                        }],
                    ),
                    ChatResponse(content="完成", model="test", usage={}, finish_reason="stop", tool_uses=None),
                ]

            def format_tool_choice(self, mode, tool_name=None):
                return {"mode": mode, "name": tool_name}

            def chat_stream_response(self, *args, **kwargs):
                raise NotImplementedError

            def chat(self, messages, **kwargs):
                self.calls.append((messages, kwargs))
                return self.responses.pop(0)

        class PptRenderer:
            def spec(self):
                return ToolSpec(
                    name="PptRenderer",
                    description="Render pptx",
                    input_schema={
                        "type": "object",
                        "properties": {"slides": {"type": "integer"}},
                        "required": ["slides"],
                    },
                    capability=ToolCapability(namespace="artifact.pptx", output_modes=("pptx",)),
                )

            def run(self, tool_input, context):
                with zipfile.ZipFile(artifact, "w") as archive:
                    for index in range(1, tool_input["slides"] + 1):
                        archive.writestr(f"ppt/slides/slide{index}.xml", "<p:sld/>")
                return ToolResult(name="PptRenderer", output={"file_path": str(artifact), "slide_count": 2})

        provider = ChoiceProvider()
        result = run_agent_loop(
            conversation=conversation,
            provider=provider,
            tool_registry=ToolRegistry([TodoWriteTool(), PptRenderer()]),
            tool_context=self.context,
            max_turns=5,
        )

        self.assertEqual(provider.calls[0][1]["tool_choice"], {"mode": "specific", "name": "TodoWrite"})
        self.assertEqual([item["name"] for item in provider.calls[0][1]["tools"]], ["TodoWrite"])
        self.assertEqual({item["name"] for item in provider.calls[1][1]["tools"]}, {"TodoWrite", "PptRenderer"})
        self.assertEqual(result.output_contract_status, "satisfied")
        self.assertEqual(result.run_state["plan_revision"], 2)

    def test_agent_loop_stops_after_runtime_cancellation_signal(self):
        conversation = Conversation()
        conversation.add_user_message("Do the task")
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()

        def cancel_during_model(*args, **kwargs):
            self.context.request_cancel()
            return ChatResponse(content="late response", model="test-model", usage={}, finish_reason="stop", tool_uses=None)

        provider.chat.side_effect = cancel_during_model
        with self.assertRaises(AgentRunCancelled):
            run_agent_loop(
                conversation=conversation,
                provider=provider,
                tool_registry=self.registry,
                tool_context=self.context,
            )

    def test_agent_loop_creates_hello_world(self):
        """Test agent loop creates hello.py and writes print('hello world')."""
        conversation = Conversation()
        conversation.add_user_message("Create a file hello.py with content print('hello world')")

        mock_provider = MagicMock()
        mock_provider.chat_stream_response.side_effect = NotImplementedError()

        # First response: tool use Write
        hello_path = self.workspace / "hello.py"
        mock_tool_write = {
            "id": "toolu_123",
            "name": "Write",
            "input": {
                "file_path": str(hello_path),
                "content": "print('hello world')"
            }
        }
        mock_response1 = ChatResponse(
            content="I will create the file.",
            model="test-model",
            usage={"input_tokens": 10, "output_tokens": 20},
            finish_reason="tool_use",
            tool_uses=[mock_tool_write],
        )

        # Second response: final
        mock_response2 = ChatResponse(
            content="File created successfully!",
            model="test-model",
            usage={"input_tokens": 30, "output_tokens": 10},
            finish_reason="stop",
            tool_uses=None,
        )

        mock_provider.chat.side_effect = [mock_response1, mock_response2]

        result = run_agent_loop(
            conversation=conversation,
            provider=mock_provider,
            tool_registry=self.registry,
            tool_context=self.context,
            verbose=False,
        )

        self.assertIsInstance(result, AgentLoopResult)
        self.assertEqual(result.response_text, "File created successfully!")
        self.assertTrue(hello_path.exists())
        self.assertEqual(hello_path.read_text(), "print('hello world')")

    def test_agent_loop_stream_emits_final_text_chunks(self):
        """Streaming mode emits final response chunks without changing the result."""
        conversation = Conversation()
        conversation.add_user_message("Say hello")

        mock_provider = MagicMock()
        mock_provider.chat_stream_response.side_effect = NotImplementedError()
        mock_provider.chat.return_value = ChatResponse(
            content="Hello from Clawd!",
            model="test-model",
            usage={"input_tokens": 3, "output_tokens": 4},
            finish_reason="stop",
            tool_uses=None,
        )

        chunks: list[str] = []
        result = run_agent_loop(
            conversation=conversation,
            provider=mock_provider,
            tool_registry=self.registry,
            tool_context=self.context,
            stream=True,
            verbose=False,
            on_text_chunk=chunks.append,
        )

        self.assertEqual("".join(chunks), "Hello from Clawd!")
        self.assertEqual(result.response_text, "Hello from Clawd!")
        self.assertEqual(mock_provider.chat.call_count, 1)
        self.assertEqual(len(conversation.messages), 2)
        self.assertEqual(conversation.messages[-1].role, "assistant")
        self.assertEqual(conversation.messages[-1].content, "Hello from Clawd!")

    def test_agent_loop_stream_only_emits_final_turn_text(self):
        """Streaming mode skips interim tool-planning text and emits the final answer only."""
        conversation = Conversation()
        conversation.add_user_message("Create a file hello.py with content print('hello world')")

        mock_provider = MagicMock()
        mock_provider.chat_stream_response.side_effect = NotImplementedError()
        hello_path = self.workspace / "hello.py"
        mock_response1 = ChatResponse(
            content="I will create the file.",
            model="test-model",
            usage={"input_tokens": 10, "output_tokens": 20},
            finish_reason="tool_use",
            tool_uses=[{
                "id": "toolu_123",
                "name": "Write",
                "input": {
                    "file_path": str(hello_path),
                    "content": "print('hello world')",
                },
            }],
        )
        mock_response2 = ChatResponse(
            content="File created successfully!",
            model="test-model",
            usage={"input_tokens": 30, "output_tokens": 10},
            finish_reason="stop",
            tool_uses=None,
        )
        mock_provider.chat.side_effect = [mock_response1, mock_response2]

        chunks: list[str] = []
        result = run_agent_loop(
            conversation=conversation,
            provider=mock_provider,
            tool_registry=self.registry,
            tool_context=self.context,
            stream=True,
            verbose=False,
            on_text_chunk=chunks.append,
        )

        self.assertEqual("".join(chunks), "File created successfully!")
        self.assertEqual(result.response_text, "File created successfully!")
        self.assertTrue(hello_path.exists())

    def test_agent_loop_stream_uses_structured_provider_streaming_for_tool_turns(self):
        """Structured provider streaming can emit pre-tool text and final text across turns."""
        conversation = Conversation()
        conversation.add_user_message("Create hello.py")

        provider = MagicMock()
        hello_path = self.workspace / "hello.py"

        stream_responses = [
            ChatResponse(
                content="I will create the file.",
                model="test-model",
                usage={"input_tokens": 10, "output_tokens": 20},
                finish_reason="tool_use",
                tool_uses=[{
                    "id": "toolu_123",
                    "name": "Write",
                    "input": {
                        "file_path": str(hello_path),
                        "content": "print('hello world')",
                    },
                }],
            ),
            ChatResponse(
                content="File created successfully!",
                model="test-model",
                usage={"input_tokens": 30, "output_tokens": 10},
                finish_reason="stop",
                tool_uses=None,
            ),
        ]

        def stream_side_effect(messages, tools=None, on_text_chunk=None, **kwargs):
            response = stream_responses.pop(0)
            if on_text_chunk is not None and response.content:
                on_text_chunk(response.content)
            return response

        provider.chat_stream_response.side_effect = stream_side_effect
        provider.chat.side_effect = AssertionError("chat() should not be used when structured streaming is available")

        chunks: list[str] = []
        result = run_agent_loop(
            conversation=conversation,
            provider=provider,
            tool_registry=self.registry,
            tool_context=self.context,
            stream=True,
            verbose=False,
            on_text_chunk=chunks.append,
        )

        self.assertEqual("".join(chunks), "File created successfully!")
        self.assertEqual(result.response_text, "File created successfully!")
        self.assertEqual(provider.chat_stream_response.call_count, 2)
        self.assertEqual([message.role for message in conversation.messages], ["user", "assistant"])
        self.assertEqual(conversation.messages[-1].content, "File created successfully!")
        self.assertTrue(hello_path.exists())

    def test_agent_loop_stream_falls_back_when_structured_streaming_is_unavailable(self):
        """If the provider lacks structured streaming, the stable synchronous path still works."""
        conversation = Conversation()
        conversation.add_user_message("Say hello")

        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.return_value = ChatResponse(
            content="Hello from fallback!",
            model="test-model",
            usage={"input_tokens": 2, "output_tokens": 3},
            finish_reason="stop",
            tool_uses=None,
        )

        chunks: list[str] = []
        result = run_agent_loop(
            conversation=conversation,
            provider=provider,
            tool_registry=self.registry,
            tool_context=self.context,
            stream=True,
            verbose=False,
            on_text_chunk=chunks.append,
        )

        self.assertEqual("".join(chunks), "Hello from fallback!")
        self.assertEqual(result.response_text, "Hello from fallback!")
        provider.chat.assert_called_once()

    def test_agent_loop_synthesizes_when_tool_budget_is_reached(self):
        """The max-turn boundary should produce a no-tool synthesis, not a sentinel answer."""
        conversation = Conversation()
        conversation.add_user_message("Research X9 brakes")

        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.side_effect = [
            ChatResponse(
                content="I will fetch evidence.",
                model="test-model",
                usage={"input_tokens": 10, "output_tokens": 5},
                finish_reason="tool_use",
                tool_uses=[{
                    "id": "toolu_1",
                    "name": "WebSearch",
                    "input": {"query": "X9 brakes"},
                }],
            ),
            ChatResponse(
                content="Based on the gathered evidence, the answer is partial but useful.",
                model="test-model",
                usage={"input_tokens": 20, "output_tokens": 12},
                finish_reason="stop",
                tool_uses=None,
            ),
        ]

        chunks: list[str] = []
        result = run_agent_loop(
            conversation=conversation,
            provider=provider,
            tool_registry=self.registry,
            tool_context=self.context,
            max_turns=1,
            stream=True,
            on_text_chunk=chunks.append,
        )

        self.assertNotEqual(result.response_text, "[Max tool turns reached]")
        self.assertIn("partial but useful", result.response_text)
        self.assertEqual("".join(chunks), result.response_text)
        self.assertEqual(conversation.messages[-1].role, "assistant")
        self.assertEqual(conversation.messages[-1].metadata.get("mode"), "tool_loop_synthesis")
        self.assertIsNone(provider.chat.call_args_list[-1].kwargs.get("tools"))

    def test_agent_loop_blocks_repeated_websearch_failures(self):
        """Repeated WebSearch failures should not keep hitting the network."""
        conversation = Conversation()
        conversation.add_user_message("Research a topic")

        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.side_effect = [
            ChatResponse(
                content="Search 1",
                model="test-model",
                usage={},
                finish_reason="tool_use",
                tool_uses=[{"id": "s1", "name": "WebSearch", "input": {"query": "q1"}}],
            ),
            ChatResponse(
                content="Search 2",
                model="test-model",
                usage={},
                finish_reason="tool_use",
                tool_uses=[{"id": "s2", "name": "WebSearch", "input": {"query": "q2"}}],
            ),
            ChatResponse(
                content="Search 3",
                model="test-model",
                usage={},
                finish_reason="tool_use",
                tool_uses=[{"id": "s3", "name": "WebSearch", "input": {"query": "q3"}}],
            ),
            ChatResponse(
                content="Search 4",
                model="test-model",
                usage={},
                finish_reason="tool_use",
                tool_uses=[{"id": "s4", "name": "WebSearch", "input": {"query": "q4"}}],
            ),
            ChatResponse(
                content="Search unavailable; here is the best answer from current evidence.",
                model="test-model",
                usage={},
                finish_reason="stop",
                tool_uses=None,
            ),
        ]

        class EmptyWebSearchTool:
            def spec(self):
                return ToolSpec(
                    name="WebSearch",
                    description="Fake search",
                    input_schema={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
                    is_read_only=True,
                )

            def run(self, tool_input, context):
                return ToolResult(name="WebSearch", output={"query": tool_input["query"], "results": []})

        events = []
        result = run_agent_loop(
            conversation=conversation,
            provider=provider,
            tool_registry=ToolRegistry([EmptyWebSearchTool()]),
            tool_context=self.context,
            max_turns=8,
            on_event=events.append,
        )

        websearch_uses = [event for event in events if event.kind == "tool_use" and event.tool_name == "WebSearch"]
        websearch_errors = [event for event in events if event.kind == "tool_error" and event.tool_name == "WebSearch"]
        self.assertEqual(len(websearch_uses), 2)
        self.assertEqual(len(websearch_errors), 2)
        self.assertIn("best answer", result.response_text)

    def test_agent_loop_blocks_equivalent_tool_calls_and_synthesizes(self):
        """Equivalent tool calls are dispatched once and then yield to synthesis."""
        conversation = Conversation()
        conversation.add_user_message("Find the same fact")

        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.side_effect = [
            *[
                ChatResponse(
                    content="Query again",
                    model="test-model",
                    usage={},
                    finish_reason="tool_use",
                    tool_uses=[{"id": f"d{index}", "name": "Lookup", "input": {"key": "same"}}],
                )
                for index in range(3)
            ],
            ChatResponse(
                content="Synthesized from the first result.",
                model="test-model",
                usage={},
                finish_reason="stop",
                tool_uses=None,
            ),
        ]

        class LookupTool:
            calls = 0

            def spec(self):
                return ToolSpec(
                    name="Lookup",
                    description="Fake lookup",
                    input_schema={"type": "object", "properties": {"key": {"type": "string"}}},
                    is_read_only=True,
                )

            def run(self, tool_input, context):
                self.calls += 1
                return ToolResult(name="Lookup", output={"value": "evidence"})

        tool = LookupTool()
        events = []
        result = run_agent_loop(
            conversation=conversation,
            provider=provider,
            tool_registry=ToolRegistry([tool]),
            tool_context=self.context,
            max_turns=8,
            on_event=events.append,
        )

        self.assertEqual(tool.calls, 1)
        self.assertEqual(len([event for event in events if event.kind == "tool_error"]), 2)
        self.assertEqual(result.response_text, "Synthesized from the first result.")

    def test_agent_loop_does_not_stop_productive_fetches_at_arbitrary_count(self):
        """Distinct successful evidence calls are not stopped by a fixed count heuristic."""
        conversation = Conversation()
        conversation.add_user_message("Research a topic")

        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.side_effect = [
            *[
                ChatResponse(
                    content=f"Fetch {index}",
                    model="test-model",
                    usage={},
                    finish_reason="tool_use",
                    tool_uses=[{"id": f"f{index}", "name": "WebFetch", "input": {"url": f"https://example.com/{index}"}}],
                )
                for index in range(5)
            ],
            ChatResponse(
                content="Synthesized after enough evidence.",
                model="test-model",
                usage={},
                finish_reason="stop",
                tool_uses=None,
            ),
        ]

        class OkWebFetchTool:
            def spec(self):
                return ToolSpec(
                    name="WebFetch",
                    description="Fake fetch",
                    input_schema={"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
                    is_read_only=True,
                )

            def run(self, tool_input, context):
                return ToolResult(name="WebFetch", output={"url": tool_input["url"], "content_type": "text/html", "content": "evidence"})

        events = []
        result = run_agent_loop(
            conversation=conversation,
            provider=provider,
            tool_registry=ToolRegistry([OkWebFetchTool()]),
            tool_context=self.context,
            max_turns=10,
            on_event=events.append,
        )

        webfetch_uses = [event for event in events if event.kind == "tool_use" and event.tool_name == "WebFetch"]
        self.assertEqual(len(webfetch_uses), 5)
        self.assertEqual(result.response_text, "Synthesized after enough evidence.")
        replan_events = [
            event for event in self.context.audit_events
            if event.get("event") == "agent_replan_requested"
        ]
        self.assertTrue(replan_events)
        state = self.context.runtime_state["agent_run_state"]
        self.assertEqual(state["evidence_count"], 5)
        self.assertGreaterEqual(state["replan_requests"], 1)

    def test_agent_loop_harness_registers_and_repairs_citations(self):
        """Harness, not the model, assigns citation ids and asks for citation repair."""
        conversation = Conversation()
        conversation.add_user_message("Compare X9 and MEGA wheelbase")

        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.side_effect = [
            ChatResponse(
                content="I will query the data.",
                model="test-model",
                usage={"input_tokens": 10, "output_tokens": 5},
                finish_reason="tool_use",
                tool_uses=[{"id": "e1", "name": "FakeEvidence", "input": {"metric": "wheelbase"}}],
            ),
            ChatResponse(
                content="X9 is shorter than MEGA.",
                model="test-model",
                usage={"input_tokens": 20, "output_tokens": 8},
                finish_reason="stop",
                tool_uses=None,
            ),
            ChatResponse(
                content="X9 is shorter than MEGA based on the wheelbase comparison [1].\n\n证据来源\n[1] SQL result.",
                model="test-model",
                usage={"input_tokens": 30, "output_tokens": 12},
                finish_reason="stop",
                tool_uses=None,
            ),
        ]

        class FakeEvidenceTool:
            def spec(self):
                return ToolSpec(
                    name="FakeEvidence",
                    description="Returns evidence",
                    input_schema={"type": "object", "properties": {"metric": {"type": "string"}}, "required": ["metric"]},
                    is_read_only=True,
                )

            def run(self, tool_input, context):
                return ToolResult(
                    name="FakeEvidence",
                    output={
                        "rows": [{"vehicle": "X9", "wheelbase": 3160}, {"vehicle": "MEGA", "wheelbase": 3300}],
                        "evidence": [
                            {
                                "source_type": "sql",
                                "title": "SQL result",
                                "source": "vehicle_attributes",
                                "content": "X9 wheelbase=3160; MEGA wheelbase=3300",
                            }
                        ],
                    },
                )

        chunks: list[str] = []
        result = run_agent_loop(
            conversation=conversation,
            provider=provider,
            tool_registry=ToolRegistry([FakeEvidenceTool()]),
            tool_context=self.context,
            stream=True,
            on_text_chunk=chunks.append,
        )

        self.assertIn("[1]", result.response_text)
        self.assertEqual("".join(chunks), result.response_text)
        self.assertEqual(provider.chat.call_count, 3)
        self.assertEqual(result.citations and result.citations[0]["citation_id"], 1)
        self.assertEqual(result.citations and result.citations[0]["source_type"], "sql")

        second_call_messages = provider.chat.call_args_list[1].args[0]
        tool_message = next(message for message in second_call_messages if message.get("role") == "tool")
        self.assertIn("citation_note", tool_message["content"])
        self.assertIn("[1]", tool_message["content"])
        self.assertEqual(conversation.messages[-1].content, result.response_text)

    def test_agent_loop_routes_vehicle_specs_to_sql_tool_surface(self):
        """Vehicle spec questions should narrow the first tool surface toward SQL/data tools."""
        conversation = Conversation()
        conversation.add_user_message("调研 小米SU7 车长")

        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.return_value = ChatResponse(
            content="小米SU7车长应从结构化数据库查询。",
            model="test-model",
            usage={"input_tokens": 10, "output_tokens": 5},
            finish_reason="stop",
            tool_uses=None,
        )

        result = run_agent_loop(
            conversation=conversation,
            provider=provider,
            tool_registry=self.registry,
            tool_context=self.context,
            max_turns=2,
        )

        self.assertIn("结构化数据库", result.response_text)
        call_kwargs = provider.chat.call_args_list[0].kwargs
        tool_names = {tool["name"] for tool in call_kwargs["tools"]}
        self.assertIn("SubjectsAttributeLookup", tool_names)
        self.assertIn("SubjectsDataCatalogSearch", tool_names)
        self.assertIn("SubjectsSqlQuery", tool_names)
        self.assertNotIn("SubjectsSqlSchema", tool_names)
        self.assertNotIn("KnowledgeSearch", tool_names)
        self.assertNotIn("WebFetch", tool_names)
        self.assertNotIn("WebSearch", tool_names)
        self.assertNotIn("Write", tool_names)
        self.assertNotIn("Bash", tool_names)
        self.assertNotIn("AutoPptxGenerate", tool_names)

        first_call_messages = provider.chat.call_args.args[0]
        system_prompt = first_call_messages[0]["content"]
        self.assertIn("route: vehicle_spec", system_prompt)
        self.assertIn("structured data discovery", system_prompt)
        self.assertIn("first call SubjectsAttributeLookup", system_prompt)

    def test_model_tool_hints_do_not_hide_route_preferred_lookup(self):
        conversation = Conversation()
        conversation.add_user_message("帮我调研全部MPV具备前备箱的情况。")

        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.side_effect = [
            ChatResponse(
                content="先规划",
                model="test-model",
                usage={"input_tokens": 10, "output_tokens": 5},
                finish_reason="tool_use",
                tool_uses=[
                    {
                        "id": "todo_1",
                        "name": "TodoWrite",
                        "input": {
                            "todos": [
                                {
                                    "content": "查询MPV与前备箱字段",
                                    "status": "in_progress",
                                    "activeForm": "查询字段",
                                    "toolHints": ["SubjectsSqlQuery"],
                                }
                            ]
                        },
                    }
                ],
            ),
            ChatResponse(
                content="更新计划",
                model="test-model",
                usage={"input_tokens": 10, "output_tokens": 5},
                finish_reason="tool_use",
                tool_uses=[
                    {
                        "id": "todo_2",
                        "name": "TodoWrite",
                        "input": {
                            "todos": [
                                {
                                    "content": "查询MPV与前备箱字段",
                                    "status": "completed",
                                    "activeForm": "查询字段",
                                    "toolHints": ["SubjectsSqlQuery"],
                                }
                            ]
                        },
                    }
                ],
            ),
            ChatResponse(
                content="完成",
                model="test-model",
                usage={"input_tokens": 10, "output_tokens": 5},
                finish_reason="stop",
                tool_uses=None,
            ),
        ]

        run_agent_loop(
            conversation=conversation,
            provider=provider,
            tool_registry=self.registry,
            tool_context=self.context,
            max_turns=2,
        )

        second_call_tools = {tool["name"] for tool in provider.chat.call_args_list[1].kwargs["tools"]}
        self.assertIn("SubjectsSqlQuery", second_call_tools)
        self.assertIn("SubjectsAttributeLookup", second_call_tools)
        self.assertIn("SubjectsDataCatalogSearch", second_call_tools)

    def test_no_data_expands_deferred_fallback_tool_schemas(self):
        conversation = Conversation()
        conversation.add_user_message("调研 小米SU7 车长")

        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.side_effect = [
            ChatResponse(
                content="Check governed data first",
                model="test-model",
                usage={},
                finish_reason="tool_use",
                tool_uses=[
                    {
                        "id": "lookup-1",
                        "name": "SubjectsAttributeLookup",
                        "input": {"entity_keyword": "小米SU7", "attribute_keyword": "车长"},
                    }
                ],
            ),
            ChatResponse(content="当前结构化数据无结果。", model="test-model", usage={}, finish_reason="stop", tool_uses=None),
        ]

        class NoDataLookup:
            def spec(self):
                return ToolSpec(
                    name="SubjectsAttributeLookup",
                    description="Vehicle lookup",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "entity_keyword": {"type": "string"},
                            "attribute_keyword": {"type": "string"},
                        },
                        "required": ["entity_keyword", "attribute_keyword"],
                    },
                )

            def run(self, tool_input, context):
                return ToolResult(
                    name="SubjectsAttributeLookup",
                    output={
                        "row_count": 0,
                        "rows": [],
                        "coverage_boundary": "No matching rows exist in the governed dataset.",
                    },
                    outcome_status=ToolOutcomeStatus.NO_DATA,
                    reason_code="VEHICLE_ATTRIBUTE_NO_ROWS",
                )

        class DeferredTool:
            def __init__(self, name):
                self.name = name

            def spec(self):
                return ToolSpec(name=self.name, description="Deferred fallback", input_schema={"type": "object"})

            def run(self, tool_input, context):
                return ToolResult(name=self.name, output={"ok": True})

        context = ToolContext(workspace_root=self.workspace)
        registry = ToolRegistry(
            [NoDataLookup(), DeferredTool("KnowledgeSearch"), DeferredTool("WebFetch")]
        )
        run_agent_loop(
            conversation=conversation,
            provider=provider,
            tool_registry=registry,
            tool_context=context,
            verbose=False,
        )

        first_tools = {tool["name"] for tool in provider.chat.call_args_list[0].kwargs["tools"]}
        second_tools = {tool["name"] for tool in provider.chat.call_args_list[1].kwargs["tools"]}
        self.assertEqual(first_tools, {"SubjectsAttributeLookup"})
        self.assertIn("KnowledgeSearch", second_tools)
        self.assertIn("WebFetch", second_tools)
        self.assertTrue(any(event.get("event") == "tool_discovery_expanded" for event in context.audit_events))
        candidate_events = [event for event in context.audit_events if event.get("event") == "tool_candidates_exposed"]
        self.assertEqual(candidate_events[0]["discovery_stage"], "primary")
        self.assertEqual(candidate_events[-1]["discovery_stage"], "fallback")

    def test_tool_search_loads_out_of_route_capability_schema_on_next_turn(self):
        conversation = Conversation()
        conversation.add_user_message("调研 小米SU7 车长")
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.side_effect = [
            ChatResponse(
                content="Check data",
                model="test-model",
                usage={},
                finish_reason="tool_use",
                tool_uses=[{
                    "id": "lookup-1",
                    "name": "SubjectsAttributeLookup",
                    "input": {"entity_keyword": "小米SU7", "attribute_keyword": "车长"},
                }],
            ),
            ChatResponse(
                content="Discover export capability",
                model="test-model",
                usage={},
                finish_reason="tool_use",
                tool_uses=[{"id": "search-1", "name": "ToolSearch", "input": {"query": "parquet export"}}],
            ),
            ChatResponse(content="Capability loaded", model="test-model", usage={}, finish_reason="stop", tool_uses=None),
        ]

        class NoDataLookup:
            def spec(self):
                return ToolSpec(
                    name="SubjectsAttributeLookup",
                    description="Vehicle lookup",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "entity_keyword": {"type": "string"},
                            "attribute_keyword": {"type": "string"},
                        },
                        "required": ["entity_keyword", "attribute_keyword"],
                    },
                )

            def run(self, tool_input, context):
                return ToolResult(
                    name="SubjectsAttributeLookup",
                    output={
                        "rows": [],
                        "row_count": 0,
                        "coverage_boundary": "No matching rows exist in the governed dataset.",
                    },
                    outcome_status=ToolOutcomeStatus.NO_DATA,
                    reason_code="NO_ROWS",
                )

        class ParquetExport:
            def spec(self):
                return ToolSpec(
                    name="ParquetExport",
                    description="Export governed analysis data as parquet",
                    input_schema={"type": "object"},
                    capability=ToolCapability(
                        namespace="data.export",
                        actions=("export",),
                        output_modes=("parquet",),
                    ),
                )

            def run(self, tool_input, context):
                return ToolResult(name="ParquetExport", output={"ok": True})

        registry = ToolRegistry([NoDataLookup(), ParquetExport()])
        registry.register(ToolSearchTool(registry))
        run_agent_loop(
            conversation=conversation,
            provider=provider,
            tool_registry=registry,
            tool_context=self.context,
            verbose=False,
        )

        second_tools = {tool["name"] for tool in provider.chat.call_args_list[1].kwargs["tools"]}
        third_tools = {tool["name"] for tool in provider.chat.call_args_list[2].kwargs["tools"]}
        self.assertIn("ToolSearch", second_tools)
        self.assertNotIn("ParquetExport", second_tools)
        self.assertIn("ParquetExport", third_tools)

    def test_agent_loop_stages_direct_vehicle_attribute_lookup(self):
        """Direct entity-attribute questions should start with the one-step lookup only."""
        conversation = Conversation()
        conversation.add_user_message("小米SU7轴距")

        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.return_value = ChatResponse(
            content="小米SU7轴距应先一跳查询。",
            model="test-model",
            usage={"input_tokens": 10, "output_tokens": 5},
            finish_reason="stop",
            tool_uses=None,
        )

        run_agent_loop(
            conversation=conversation,
            provider=provider,
            tool_registry=self.registry,
            tool_context=self.context,
        )

        call_kwargs = provider.chat.call_args_list[0].kwargs
        tool_names = {tool["name"] for tool in call_kwargs["tools"]}
        self.assertEqual({"SubjectsAttributeLookup"}, tool_names)

    def test_text_only_vehicle_fact_does_not_satisfy_task_contract(self):
        conversation = Conversation()
        conversation.add_user_message("小米SU7轴距")
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.side_effect = [
            ChatResponse(content="轴距是3000mm。", model="test-model", usage={}, finish_reason="stop", tool_uses=None),
            ChatResponse(content="仍然只有模型记忆。", model="test-model", usage={}, finish_reason="stop", tool_uses=None),
        ]

        result = run_agent_loop(
            conversation=conversation,
            provider=provider,
            tool_registry=self.registry,
            tool_context=self.context,
            max_turns=1,
        )

        self.assertEqual(result.output_contract_status, "not_required")
        self.assertEqual(result.task_contract_status, "unmet")
        evidence_requirement = next(item for item in result.requirements if item["id"] == "evidence:structured_fact")
        self.assertEqual(evidence_requirement["status"], "open")

    def test_agent_loop_stops_tools_after_successful_attribute_lookup(self):
        """A successful staged lookup should be followed by answer synthesis, not more SQL probing."""

        class FakeSubjectsAttributeLookup:
            def spec(self) -> ToolSpec:
                return ToolSpec(
                    name="SubjectsAttributeLookup",
                    description="one-step lookup",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "entity_keyword": {"type": "string"},
                            "attribute_keyword": {"type": "string"},
                        },
                        "required": ["entity_keyword", "attribute_keyword"],
                    },
                    is_read_only=True,
                )

            def run(self, tool_input: dict, context: ToolContext) -> ToolResult:
                return ToolResult(
                    name="SubjectsAttributeLookup",
                    output={
                        "row_count": 1,
                        "rows": [{"vehicle_name": "小米SU7", "attribute_name": "轴距", "value_number": 3000, "unit": "mm"}],
                    },
                )

        conversation = Conversation()
        conversation.add_user_message("小米SU7轴距")
        registry = ToolRegistry([FakeSubjectsAttributeLookup()])

        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.side_effect = [
            ChatResponse(
                content="先查结构化属性。",
                model="test-model",
                usage={"input_tokens": 10, "output_tokens": 5},
                finish_reason="tool_use",
                tool_uses=[
                    {
                        "id": "lookup1",
                        "name": "SubjectsAttributeLookup",
                        "input": {"entity_keyword": "小米SU7", "attribute_keyword": "轴距"},
                    }
                ],
            ),
            ChatResponse(
                content="小米SU7轴距为3000mm。",
                model="test-model",
                usage={"input_tokens": 12, "output_tokens": 6},
                finish_reason="stop",
                tool_uses=None,
            ),
        ]

        result = run_agent_loop(
            conversation=conversation,
            provider=provider,
            tool_registry=registry,
            tool_context=self.context,
        )

        self.assertIn("3000", result.response_text)
        first_tools = {tool["name"] for tool in provider.chat.call_args_list[0].kwargs["tools"]}
        second_tools = provider.chat.call_args_list[1].kwargs["tools"]
        self.assertEqual({"SubjectsAttributeLookup"}, first_tools)
        self.assertEqual([], second_tools)
        self.assertEqual(result.task_contract_status, "satisfied")
        evidence_requirement = next(item for item in result.requirements if item["id"] == "evidence:structured_fact")
        self.assertEqual(evidence_requirement["status"], "satisfied")
        self.assertEqual(evidence_requirement["evidence_ids"], [1])

    def test_citation_list_prioritizes_sql(self):
        """SQL citations should be rendered before knowledge citations in the harness prompt."""
        text = _format_citation_list(
            [
                {"citation_id": 3, "source_type": "knowledge", "title": "Manual chunk", "source": "doc://manual", "content": "manual evidence", "tool_name": "KnowledgeSearch"},
                {"citation_id": 1, "source_type": "sql", "title": "SQL result", "source": "SubjectsSqlQuery", "content": "vehicle_name=X9; wheelbase=3160", "tool_name": "SubjectsSqlQuery"},
                {"citation_id": 2, "source_type": "knowledge", "title": "Other chunk", "source": "doc://other", "content": "other evidence", "tool_name": "KnowledgeSearch"},
            ]
        )
        self.assertLess(text.index("[SQL evidence]"), text.index("[Knowledge evidence]"))
        self.assertLess(text.index("[1] SQL result"), text.index("[3] Manual chunk"))

    def test_agent_loop_repairs_when_sql_evidence_is_missing_from_final_text(self):
        """If SQL evidence exists for a structured question, the final answer should be repaired to cite it."""
        conversation = Conversation()
        conversation.add_user_message("调研 小米SU7 车长")

        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.side_effect = [
            ChatResponse(
                content="I will inspect the data.",
                model="test-model",
                usage={"input_tokens": 10, "output_tokens": 5},
                finish_reason="tool_use",
                tool_uses=[
                    {"id": "k1", "name": "KnowledgeSearch", "input": {"query": "小米SU7 车长", "top_k": 5}},
                    {"id": "s1", "name": "SubjectsSqlQuery", "input": {"query": "SELECT 1 AS length_mm"}},
                ],
            ),
            ChatResponse(
                content="小米SU7车长是4997mm。",
                model="test-model",
                usage={"input_tokens": 20, "output_tokens": 8},
                finish_reason="stop",
                tool_uses=None,
            ),
            ChatResponse(
                content="小米SU7车长是4997mm [2]。\n\n证据来源\n[2] SQL result.",
                model="test-model",
                usage={"input_tokens": 30, "output_tokens": 12},
                finish_reason="stop",
                tool_uses=None,
            ),
        ]

        class FakeKnowledgeSearchTool:
            def spec(self):
                return ToolSpec(
                    name="KnowledgeSearch",
                    description="Fake knowledge search",
                    input_schema={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
                    is_read_only=True,
                )

            def run(self, tool_input, context):
                return ToolResult(
                    name="KnowledgeSearch",
                    output={"results": [{"title": "manual chunk", "source": "doc://manual", "excerpt": "manual evidence"}]},
                )

        class FakeSqlTool:
            def spec(self):
                return ToolSpec(
                    name="SubjectsSqlQuery",
                    description="Fake SQL query",
                    input_schema={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
                    is_read_only=True,
                )

            def run(self, tool_input, context):
                return ToolResult(
                    name="SubjectsSqlQuery",
                    output={
                        "query": tool_input["query"],
                        "row_count": 1,
                        "rows": [{"vehicle_name": "小米SU7 2026款 标准版", "length_mm": 4997}],
                    },
                )

        result = run_agent_loop(
            conversation=conversation,
            provider=provider,
            tool_registry=ToolRegistry([FakeKnowledgeSearchTool(), FakeSqlTool()]),
            tool_context=self.context,
            verbose=False,
        )

        self.assertIn("[2]", result.response_text)
        self.assertEqual(provider.chat.call_count, 3)
        repair_messages = provider.chat.call_args_list[2].args[0]
        repair_prompt = repair_messages[-1]["content"]
        self.assertIn("[SQL evidence]", repair_prompt)
        self.assertIn("prefer at least one SQL citation", repair_prompt)

    def test_agent_loop_repairs_when_structured_value_is_omitted(self):
        """For structured facts, SQL evidence should also trigger a rewrite when the draft omits the value itself."""
        conversation = Conversation()
        conversation.add_user_message("调研 小米SU7 车长")

        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.side_effect = [
            ChatResponse(
                content="I will inspect the data.",
                model="test-model",
                usage={"input_tokens": 10, "output_tokens": 5},
                finish_reason="tool_use",
                tool_uses=[
                    {"id": "s1", "name": "SubjectsSqlQuery", "input": {"query": "SELECT id, vehicle_name FROM vehicle_instance WHERE vehicle_name LIKE '%小米SU7%'" }},
                ],
            ),
            ChatResponse(
                content="当前证据还不足，暂时无法确认。",
                model="test-model",
                usage={"input_tokens": 20, "output_tokens": 8},
                finish_reason="stop",
                tool_uses=None,
            ),
            ChatResponse(
                content="小米SU7 2026款 标准版车长为4997mm [1]。\n\n证据来源\n[1] SQL result.",
                model="test-model",
                usage={"input_tokens": 30, "output_tokens": 12},
                finish_reason="stop",
                tool_uses=None,
            ),
        ]

        class FakeSqlTool:
            def spec(self):
                return ToolSpec(
                    name="SubjectsSqlQuery",
                    description="Fake SQL query",
                    input_schema={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
                    is_read_only=True,
                )

            def run(self, tool_input, context):
                return ToolResult(
                    name="SubjectsSqlQuery",
                    output={
                        "query": tool_input["query"],
                        "row_count": 1,
                        "rows": [{"vehicle_name": "小米SU7 2026款 标准版", "length_mm": 4997}],
                    },
                )

        result = run_agent_loop(
            conversation=conversation,
            provider=provider,
            tool_registry=ToolRegistry([FakeSqlTool()]),
            tool_context=self.context,
            verbose=False,
        )

        self.assertIn("4997", result.response_text)
        self.assertEqual(provider.chat.call_count, 3)
        repair_messages = provider.chat.call_args_list[2].args[0]
        repair_prompt = repair_messages[-1]["content"]
        self.assertIn("actual values from the SQL evidence", repair_prompt)
        self.assertIn("Do not claim the data is unavailable", repair_prompt)

    def test_preflight_rejection_does_not_consume_execution_fuse(self):
        conversation = Conversation()
        conversation.add_user_message("Do the task")

        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.side_effect = [
            ChatResponse(
                content="Try unavailable path",
                model="test-model",
                usage={},
                finish_reason="tool_use",
                tool_uses=[{"id": "bad1", "name": "Unavailable", "input": {}}],
            ),
            ChatResponse(
                content="Use eligible path",
                model="test-model",
                usage={},
                finish_reason="tool_use",
                tool_uses=[{"id": "ok1", "name": "Lookup", "input": {}}],
            ),
            ChatResponse(content="Done", model="test-model", usage={}, finish_reason="stop", tool_uses=None),
        ]

        class UnavailableTool:
            def spec(self):
                return ToolSpec(name="Unavailable", description="Unavailable", input_schema={"type": "object"})

            def preflight(self, tool_input, context):
                return PreflightDecision.reject("DEPENDENCY_NOT_CONFIGURED", "Dependency is not configured.")

            def run(self, tool_input, context):
                raise AssertionError("preflight-rejected tool must not execute")

        class LookupTool:
            calls = 0

            def spec(self):
                return ToolSpec(name="Lookup", description="Lookup", input_schema={"type": "object"})

            def run(self, tool_input, context):
                self.calls += 1
                return ToolResult(name="Lookup", output={"value": "ok"})

        lookup = LookupTool()
        events = []
        with patch.dict("os.environ", {"CLAWD_MAX_TOOL_CALLS_PER_RUN": "1"}):
            result = run_agent_loop(
                conversation=conversation,
                provider=provider,
                tool_registry=ToolRegistry([UnavailableTool(), lookup]),
                tool_context=self.context,
                on_event=events.append,
                verbose=False,
            )

        self.assertEqual(result.response_text, "Done")
        self.assertEqual(lookup.calls, 1)
        rejected = [event for event in events if event.reason_code == "DEPENDENCY_NOT_CONFIGURED"]
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0].outcome_status, "dependency_unhealthy")

    def test_output_contract_reprompts_until_real_artifact_exists(self):
        conversation = Conversation()
        conversation.add_user_message("生成2页PPT")
        artifact = self.workspace / "result.pptx"

        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.side_effect = [
            ChatResponse(content="文字版已经完成", model="test-model", usage={}, finish_reason="stop", tool_uses=None),
            ChatResponse(
                content="Now generate it",
                model="test-model",
                usage={},
                finish_reason="tool_use",
                tool_uses=[{"id": "ppt1", "name": "AutoPptxGenerate", "input": {"slide_count": 2}}],
            ),
            ChatResponse(content="PPT已生成", model="test-model", usage={}, finish_reason="stop", tool_uses=None),
        ]

        class FakePptTool:
            def spec(self):
                return ToolSpec(
                    name="AutoPptxGenerate",
                    description="Generate PPT",
                    input_schema={
                        "type": "object",
                        "properties": {"slide_count": {"type": "integer"}},
                        "required": ["slide_count"],
                    },
                )

            def run(self, tool_input, context):
                with zipfile.ZipFile(artifact, "w") as archive:
                    for index in range(1, tool_input["slide_count"] + 1):
                        archive.writestr(f"ppt/slides/slide{index}.xml", "<p:sld/>")
                return ToolResult(
                    name="AutoPptxGenerate",
                    output={"file_path": str(artifact), "slide_count": tool_input["slide_count"]},
                )

        result = run_agent_loop(
            conversation=conversation,
            provider=provider,
            tool_registry=ToolRegistry([FakePptTool()]),
            tool_context=self.context,
            verbose=False,
        )

        self.assertEqual(provider.chat.call_count, 3)
        self.assertEqual(result.output_contract_status, "satisfied")
        self.assertTrue(artifact.is_file())
        self.assertEqual(result.requirements[0]["status"], "satisfied")

    def test_independent_read_only_tools_execute_in_parallel_and_commit_in_order(self):
        conversation = Conversation()
        conversation.add_user_message("Look up both independent facts")
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.side_effect = [
            ChatResponse(
                content="Looking up both facts.",
                model="test-model",
                usage={},
                finish_reason="tool_use",
                tool_uses=[
                    {"id": "a1", "name": "LookupA", "input": {"key": "a"}},
                    {"id": "b1", "name": "LookupB", "input": {"key": "b"}},
                ],
            ),
            ChatResponse(content="Done", model="test-model", usage={}, finish_reason="stop", tool_uses=None),
        ]
        lock = threading.Lock()
        active = 0
        max_active = 0

        class ParallelLookup:
            def __init__(self, name):
                self.name = name

            def spec(self):
                return ToolSpec(
                    name=self.name,
                    description="Independent lookup",
                    input_schema={"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]},
                    is_read_only=True,
                    execution=ToolExecutionPolicy(supports_parallel=True),
                )

            def run(self, tool_input, context):
                nonlocal active, max_active
                with lock:
                    active += 1
                    max_active = max(max_active, active)
                time.sleep(0.05)
                with lock:
                    active -= 1
                return ToolResult(name=self.name, output={"value": tool_input["key"]})

        result = run_agent_loop(
            conversation=conversation,
            provider=provider,
            tool_registry=ToolRegistry([ParallelLookup("LookupA"), ParallelLookup("LookupB")]),
            tool_context=self.context,
            verbose=False,
        )

        self.assertEqual(result.response_text, "Done")
        self.assertEqual(max_active, 2)
        completed = [event for event in self.context.audit_events if event.get("event") == "parallel_tool_batch_completed"]
        self.assertEqual(completed[0]["batch_size"], 2)
        second_messages = provider.chat.call_args_list[1].args[0]
        tool_messages = [message for message in second_messages if message.get("role") == "tool"]
        self.assertEqual([message["tool_call_id"] for message in tool_messages], ["a1", "b1"])

    def test_non_parallel_tool_policy_keeps_same_turn_calls_serial(self):
        conversation = Conversation()
        conversation.add_user_message("Run both ordered actions")
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        provider.chat.side_effect = [
            ChatResponse(
                content="Running actions.",
                model="test-model",
                usage={},
                finish_reason="tool_use",
                tool_uses=[
                    {"id": "a1", "name": "ActionA", "input": {}},
                    {"id": "b1", "name": "ActionB", "input": {}},
                ],
            ),
            ChatResponse(content="Done", model="test-model", usage={}, finish_reason="stop", tool_uses=None),
        ]
        order = []

        class OrderedAction:
            def __init__(self, name):
                self.name = name

            def spec(self):
                return ToolSpec(name=self.name, description="Ordered action", input_schema={"type": "object"})

            def run(self, tool_input, context):
                order.append(self.name)
                return ToolResult(name=self.name, output={"ok": True})

        run_agent_loop(
            conversation=conversation,
            provider=provider,
            tool_registry=ToolRegistry([OrderedAction("ActionA"), OrderedAction("ActionB")]),
            tool_context=self.context,
            verbose=False,
        )

        self.assertEqual(order, ["ActionA", "ActionB"])
        self.assertFalse(any(event.get("event") == "parallel_tool_batch_started" for event in self.context.audit_events))

    def test_plan_update_can_share_turn_with_parallel_ready_tools(self):
        conversation = Conversation()
        conversation.add_user_message("汇总全部独立结果")
        provider = MagicMock()
        provider.chat_stream_response.side_effect = NotImplementedError()
        step = {"content": "Collect independent results", "activeForm": "Collecting results"}
        provider.chat.side_effect = [
            ChatResponse(
                content="Planning",
                model="test-model",
                usage={},
                finish_reason="tool_use",
                tool_uses=[{"id": "p1", "name": "TodoWrite", "input": {"todos": [{**step, "status": "in_progress"}]}}],
            ),
            ChatResponse(
                content="Updating and dispatching the ready layer",
                model="test-model",
                usage={},
                finish_reason="tool_use",
                tool_uses=[
                    {"id": "p2", "name": "TodoWrite", "input": {"todos": [{**step, "status": "completed"}]}},
                    {"id": "a", "name": "LookupA", "input": {}},
                    {"id": "b", "name": "LookupB", "input": {}},
                ],
            ),
            ChatResponse(content="Done", model="test-model", usage={}, finish_reason="stop", tool_uses=None),
        ]
        lock = threading.Lock()
        active = 0
        max_active = 0

        class ParallelLookup:
            def __init__(self, name):
                self.name = name

            def spec(self):
                return ToolSpec(
                    name=self.name,
                    description="Lookup",
                    input_schema={"type": "object"},
                    is_read_only=True,
                    execution=ToolExecutionPolicy(supports_parallel=True),
                )

            def run(self, tool_input, context):
                nonlocal active, max_active
                with lock:
                    active += 1
                    max_active = max(max_active, active)
                time.sleep(0.05)
                with lock:
                    active -= 1
                return ToolResult(name=self.name, output={"ok": True})

        result = run_agent_loop(
            conversation=conversation,
            provider=provider,
            tool_registry=ToolRegistry([TodoWriteTool(), ParallelLookup("LookupA"), ParallelLookup("LookupB")]),
            tool_context=self.context,
        )

        self.assertEqual(result.response_text, "Done")
        self.assertEqual(max_active, 2)
        self.assertEqual(provider.chat.call_count, 3)


if __name__ == "__main__":
    unittest.main()
