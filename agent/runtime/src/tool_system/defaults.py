from __future__ import annotations

from pathlib import Path

from .loader import load_tools_from_dir
from .registry import ToolRegistry
from .tools import (
    AskUserQuestionTool,
    AutoChartGenerateTool,
    AutoPptxGenerateTool,
    BashTool,
    BriefTool,
    ConfigTool,
    CronCreateTool,
    CronDeleteTool,
    CronListTool,
    EnterPlanModeTool,
    EnterWorktreeTool,
    ExitPlanModeTool,
    ExitWorktreeTool,
    FileEditTool,
    FileReadTool,
    FileWriteTool,
    GlobTool,
    GrepTool,
    KnowledgeFetchTool,
    KnowledgeSearchTool,
    LSPTool,
    ListMcpResourcesTool,
    MCPTool,
    NotebookEditTool,
    PowerShellTool,
    REPLTool,
    ReadMcpResourceTool,
    RemoteTriggerTool,
    SendMessageTool,
    SendUserMessageTool,
    SkillTool,
    SleepTool,
    StructuredOutputTool,
    SubjectsAttributeLookupTool,
    SubjectsAttributeStatsTool,
    SubjectsDataCatalogSearchTool,
    SubjectsSqlGlobTool,
    SubjectsSqlQueryTool,
    SubjectsSqlSchemaTool,
    TeamCreateTool,
    TeamDeleteTool,
    TaskCreateTool,
    TaskGetTool,
    TaskListTool,
    TaskOutputTool,
    TaskStopTool,
    TaskUpdateTool,
    TestingPermissionTool,
    TodoWriteTool,
    WebFetchTool,
    WebSearchTool,
)
from .tools.agent import AgentTool
from .tools.tool_search import ToolSearchTool


def _web_search_enabled() -> bool:
    import os

    return os.getenv("CLAWD_ENABLE_WEBSEARCH", "").strip().lower() in {"1", "true", "yes", "on"}


def _production_profile_enabled() -> bool:
    import os

    explicit = os.getenv("CLAWD_TOOL_PROFILE", "").strip().lower()
    if explicit:
        return explicit in {"production", "prod", "safe"}
    return os.getenv("APP_ENV", "local").strip().lower() not in {"local", "dev", "development", "test"}


def build_default_registry(*, include_user_tools: bool = True) -> ToolRegistry:
    tools = [
        SendUserMessageTool(),
        BashTool(),
        FileReadTool(),
        FileWriteTool(),
        FileEditTool(),
        GlobTool(),
        GrepTool(),
        KnowledgeSearchTool(),
        KnowledgeFetchTool(),
        WebFetchTool(),
        SleepTool(),
        TaskStopTool(),
        ConfigTool(),
        MCPTool(),
        ListMcpResourcesTool(),
        ReadMcpResourceTool(),
        LSPTool(),
        SkillTool(),
        BriefTool(),
        AskUserQuestionTool(),
        TodoWriteTool(),
        TaskCreateTool(),
        TaskGetTool(),
        TaskListTool(),
        TaskUpdateTool(),
        TaskOutputTool(),
        TeamCreateTool(),
        TeamDeleteTool(),
        EnterPlanModeTool(),
        ExitPlanModeTool(),
        EnterWorktreeTool(),
        ExitWorktreeTool(),
        CronCreateTool(),
        CronListTool(),
        CronDeleteTool(),
        SendMessageTool(),
        SubjectsAttributeLookupTool(),
        SubjectsAttributeStatsTool(),
        SubjectsDataCatalogSearchTool(),
        SubjectsSqlSchemaTool(),
        SubjectsSqlGlobTool(),
        SubjectsSqlQueryTool(),
        AutoChartGenerateTool(),
        AutoPptxGenerateTool(),
        StructuredOutputTool(),
        RemoteTriggerTool(),
        PowerShellTool(),
        NotebookEditTool(),
        REPLTool(),
        TestingPermissionTool(),
    ]
    if _web_search_enabled():
        tools.append(WebSearchTool())

    if _production_profile_enabled():
        safe_names = {
            "SendUserMessage",
            "TodoWrite",
            "KnowledgeSearch",
            "KnowledgeFetch",
            "WebSearch",
            "WebFetch",
            "SubjectsAttributeLookup",
            "SubjectsAttributeStats",
            "SubjectsDataCatalogSearch",
            "SubjectsSqlSchema",
            "SubjectsSqlGlob",
            "SubjectsSqlQuery",
            "AutoChartGenerate",
            "AutoPptxGenerate",
            "StructuredOutput",
        }
        tools = [tool for tool in tools if tool.spec().name in safe_names]

    registry = ToolRegistry(
        tools=tools
    )
    if not _production_profile_enabled():
        registry.register(AgentTool(registry))
    registry.register(ToolSearchTool(registry))

    if include_user_tools and not _production_profile_enabled():
        user_dir = Path.home() / ".clawd" / "tools"
        for tool in load_tools_from_dir(user_dir):
            registry.register(tool)

    return registry
