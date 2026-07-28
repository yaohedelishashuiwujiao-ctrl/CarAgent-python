"""Agent loop for multi-turn tool calling.

这个文件是整个 Agent 系统的核心——Agent 主循环。
核心函数 run_agent_loop() 实现了一个「思考→调工具→再思考」的循环：
  1. 把用户问题 + 对话历史 + 可用工具列表 交给大模型
  2. 大模型要么直接回答，要么要求调用工具
  3. 如果调工具，执行后把结果塞回对话，回到第 1 步
  4. 直到模型不再调工具，或触发安全停止条件

文件结构：
  L1-244    导入、辅助函数、数据类型定义
  L246-532  路由系统（判断用户问的是什么类型，决定暴露哪些工具）
  L535-1177 辅助函数（证据提取、引用管理、格式化等）
  L1181-2684 核心函数 run_agent_loop()（Agent 主循环）
"""

from __future__ import annotations

import json
import hashlib
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit

from .registry import ToolRegistry, preflight_outcome_status
from .context import ToolContext
from .preflight import EligibilityStatus
from .protocol import ToolCall, ToolOutcomeStatus
from .run_budget import RunBudget
from .scheduler import ToolCallScheduler
from .run_state import AgentRunState
from .task_contract import TaskRequirementState
from ..routing_decision import decide_route
from ..agent.conversation import Conversation, TextContentBlock, ToolUseContentBlock
from ..context_system import build_context_prompt
from ..context_system.budget import prepare_messages_with_budget
from ..outputStyles import resolve_output_style
from ..providers.base import BaseProvider, ChatResponse
from ..providers.anthropic_provider import AnthropicProvider
from ..providers.minimax_provider import MinimaxProvider


# ==================== 辅助函数区（L32-244）====================
# 这些函数服务于主循环，但不是主循环本身。
# 包括：异常类、工具结果压缩、指纹去重、结果摘要、数据类型定义

class AgentRunCancelled(RuntimeError):
    """Agent 被用户取消时抛出这个异常"""
    pass


def _max_parallel_tools() -> int:
    """单次模型回合中，最多并行执行几个工具调用。默认 4，最大 16。"""
    try:
        return max(1, min(16, int(os.getenv("CLAWD_MAX_PARALLEL_TOOLS", "4"))))
    except ValueError:
        return 4


def _result_disables_tool_for_run(result: Any) -> bool:
    """工具返回这个结果后，是否要在本轮禁用该工具。
    禁用场景：依赖不健康（如数据库挂了）、能力不匹配、权限拒绝。
    注意：SQL 数据范围拒绝不禁用，因为模型可以修正查询条件。"""
    if result.outcome_status in {
        ToolOutcomeStatus.DEPENDENCY_UNHEALTHY,
        ToolOutcomeStatus.CAPABILITY_MISMATCH,
    }:
        return not result.retryable
    if result.outcome_status == ToolOutcomeStatus.PERMISSION_DENIED:
        # This SQL policy is input-specific: the model can correct the query by
        # adding the required active-record scope. It is not a tool-wide denial.
        return result.reason_code != "SQL_DATA_SCOPE_REJECTED"
    return False


def _result_indicates_tool_health_failure(result: Any) -> bool:
    """工具结果是否表示「工具本身出了问题」（区别于「没查到数据」）。
    用于统计工具失败次数，连续失败 2 次就禁用。"""
    return result.outcome_status in {
        ToolOutcomeStatus.DEPENDENCY_UNHEALTHY,
        ToolOutcomeStatus.TRANSIENT_FAILURE,
        ToolOutcomeStatus.PERMANENT_FAILURE,
        ToolOutcomeStatus.TIMEOUT,
    }


def _is_anthropic_provider(provider: BaseProvider) -> bool:
    return isinstance(provider, (AnthropicProvider, MinimaxProvider))


def _build_openai_tool_result_content(result_output: Any) -> str:
    """Format tool result as string for OpenAI/GLM."""
    if isinstance(result_output, str):
        return result_output
    return json.dumps(result_output, ensure_ascii=False, default=str)


def _compact_model_observation(tool_name: str, result: ToolResult, result_output: Any) -> Any:
    """压缩工具结果，只保留模型需要的关键信息。
    模型不需要看完整的 SQL 结果或知识块原文，
    只需要知道：查到了什么、有多少条、是否成功。
    完整数据在审计日志和引用里随时可以取。"""
    if not isinstance(result_output, dict):
        text = str(result_output)
        return text if len(text) <= 1200 else text[:1197] + "..."

    output = result_output
    compact: dict[str, Any] = {
        "tool": tool_name,
        "outcome_status": getattr(result.outcome_status, "value", str(result.outcome_status)),
        "reason_code": result.reason_code,
    }
    for key in (
        "query",
        "entity_keyword",
        "attribute_keyword",
        "filter_attribute_keyword",
        "filter_value_keyword",
        "row_count",
        "match_count",
        "candidate_count",
        "populated_value_count",
        "filtered_entity_count",
        "coverage_boundary",
        "advice",
        "error",
        "citation_note",
        "citation_ids",
    ):
        if key in output:
            compact[key] = output.get(key)

    if isinstance(output.get("attribute_candidates"), list):
        compact["attribute_candidates"] = output["attribute_candidates"][:3]
    if isinstance(output.get("filter_attribute_candidates"), list):
        compact["filter_attribute_candidates"] = output["filter_attribute_candidates"][:3]
    if isinstance(output.get("matches"), list):
        compact["matches"] = output["matches"][:3]
    if isinstance(output.get("rows"), list):
        compact["rows"] = output["rows"][:3]
        compact["rows_truncated_for_model"] = len(output["rows"]) > 3
    if tool_name == "SubjectsSqlSchema" and isinstance(output.get("tables"), list):
        tables = output["tables"]
        compact["tables"] = tables[:12]
        compact["tables_truncated_for_model"] = len(tables) > 12
        if "core_tables" in output:
            compact["core_tables"] = output.get("core_tables")
    return compact


def _stable_fingerprint(value: Any) -> str:
    """对任意值算 SHA256 指纹，用于判断两次工具调用是否完全相同（去重用）。"""
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

def summarize_tool_result(name: str, output: Any) -> str:
    """生成工具结果的单行摘要，用于终端日志打印。
    比如 'KnowledgeSearch · query=悬架 · results=5'
    不同工具有不同格式，这里按工具名分别处理。"""
    if not isinstance(output, dict):
        return str(output)
    if name.lower() == "write":
        path = output.get("filePath") or output.get("file_path")
        op = output.get("type")
        return f"{name} · {path} · {op}"
    if name.lower() == "edit":
        path = output.get("filePath") or output.get("file_path")
        replace_all = output.get("replaceAll")
        return f"{name} · {path} · replaceAll={replace_all}"
    if name.lower() == "read":
        if output.get("type") == "text" and isinstance(output.get("file"), dict):
            f = output["file"]
            path = f.get("filePath")
            num = f.get("numLines")
            total = f.get("totalLines")
            start = f.get("startLine")
            return f"{name} · {path} · lines={start}-{(start or 1) + (num or 0) - 1}/{total}"
        if output.get("type") == "file_unchanged" and isinstance(output.get("file"), dict):
            return f"{name} · {output['file'].get('filePath')} · unchanged"
        if output.get("type") in {"image", "pdf", "notebook"} and isinstance(output.get("file"), dict):
            return f"{name} · {output['file'].get('filePath')} · {output.get('type')}"
        return f"{name}"
    if name.lower() == "glob":
        n = output.get("numFiles")
        return f"{name} · matches={n}"
    if name.lower() == "grep":
        n = output.get("numFiles")
        mode = output.get("mode")
        return f"{name} · mode={mode} · files={n}"
    if name.lower() == "bash":
        code = output.get("exit_code")
        return f"{name} · exit={code}"
    if name.lower() == "webfetch":
        url = output.get("url")
        ct = output.get("content_type")
        return f"{name} · {url} · {ct}"
    if name.lower() == "websearch":
        q = output.get("query")
        results = output.get("results")
        n = len(results) if isinstance(results, list) else None
        return f"{name} · \"{q}\" · results={n}"
    if name.lower() == "config":
        op = output.get("operation")
        setting = output.get("setting")
        return f"{name} · {op} · {setting}"
    if name.lower() == "autochartgenerate":
        return f"{name} · {output.get('chart_type')} · rows={output.get('row_count')} · {output.get('file_path')}"
    if name.lower() == "autopptxgenerate":
        return f"{name} · slides={output.get('slide_count')} · {output.get('file_path')}"
    if name.lower() == "taskstop":
        tid = output.get("task_id")
        stopped = output.get("stopped")
        return f"{name} · {tid} · stopped={stopped}"
    if name.lower() == "sendusermessage":
        n = 0
        atts = output.get("attachments")
        if isinstance(atts, list):
            n = len(atts)
        return f"{name} · attachments={n}"
    # default: truncate dict keys for brevity
    keys = ", ".join(list(output.keys())[:3])
    return f"{name} · {keys}"


# ==================== 数据类型定义（L204-266）====================

@dataclass(frozen=True)
class ToolEvent:
    """工具事件，通过 on_event 回调发给前端（SSE 事件）。
    kind 取值：tool_use（开始调用）、tool_result（调用完成）、tool_error（调用出错）"""
    kind: str
    tool_name: str
    tool_input: dict[str, Any] | None = None
    tool_output: Any | None = None
    tool_use_id: str | None = None
    is_error: bool = False
    error: str | None = None
    outcome_status: str | None = None
    reason_code: str | None = None
    retryable: bool = False


@dataclass(frozen=True)
class AgentLoopResult:
    """Agent 主循环的最终返回值，包含回答文本、token 用量、引用列表等所有元数据。
    这个结果会被序列化后通过 SSE 的 final 事件发给前端。"""
    response_text: str
    usage: dict[str, Any] | None = None  # {"input_tokens": int, "output_tokens": int}
    num_turns: int = 0
    citations: list[dict[str, Any]] | None = None
    claims: list[dict[str, Any]] | None = None
    evidence_status: str = "not_applicable"
    route: str = "general"
    route_policy_version: str = "v1"
    output_contract_status: str = "not_required"
    task_contract_status: str = "not_required"
    requirements: list[dict[str, Any]] | None = None
    termination_reason: str = "completed"
    run_state: dict[str, Any] | None = None
    tool_scheduler_ledger: dict[str, Any] | None = None
    run_budget: dict[str, Any] | None = None
    route_decision: dict[str, Any] | None = None
    model_tier: str = "standard"
    budget_class: str = "normal"
    model_routing: dict[str, Any] | None = None


ToolEventHandler = Callable[[ToolEvent], None]
TextChunkHandler = Callable[[str], None]


# ==================== 路由系统（L246-532）====================
# 路由系统的作用：根据用户问题判断属于哪个业务领域，
# 然后只给模型暴露该领域需要的工具，避免工具太多导致模型混乱。
#
# 比如用户问「车长多少」→ 路由到 vehicle_spec → 只暴露属性查询、SQL 查询等工具
# 比如用户问「说明书上怎么写」→ 路由到 manual_qa → 只暴露知识检索工具

@dataclass(frozen=True)
class RoutePolicy:
    """路由策略，决定本轮给模型暴露哪些工具。"""

    route: str
    reason: str
    preferred_tools: tuple[str, ...]
    fallback_tools: tuple[str, ...] = ()
    guidance: str = ""

    @property
    def tool_allowlist(self) -> set[str]:
        return {name.lower() for name in (*self.preferred_tools, *self.fallback_tools)}


# 通用路由：没有匹配到任何专业领域时走这个，暴露全部工具
GENERAL_ROUTE = RoutePolicy(
    route="general",
    reason="No narrow data/source route matched; expose the full tool surface.",
    preferred_tools=(),
    guidance="Use the tool best suited to the user's task. Prefer evidence-backed answers when factual claims are made.",
)


def _last_user_text(conversation: Conversation) -> str:
    """从对话历史中取最后一条用户消息的文本，用于路由判断。"""
    for message in reversed(conversation.messages):
        if message.role != "user":
            continue
        content = message.content
        if isinstance(content, str):
            return content
        try:
            return json.dumps(content, ensure_ascii=False, default=str)
        except Exception:
            return str(content)
    return ""


@dataclass(frozen=True)
class RouteCard:
    """路由卡片，每张卡片代表一个业务领域。
    包含：领域名称、匹配信号（关键词+权重）、优先工具、备用工具、给模型的指导语。
    路由判断时，遍历所有卡片，算分，得分最高的胜出。"""
    route: str
    reason: str
    preferred_tools: tuple[str, ...]
    fallback_tools: tuple[str, ...]
    guidance: str
    signals: tuple[tuple[str, float], ...]

    def score(self, text: str) -> float:
        """计算用户文本在这张卡片上的得分。
        原理很简单：用户文本里出现了哪个信号词，就把对应权重加起来。
        比如用户说「车长多少」，命中「车长」(5.0) → 总分 5.0"""
        lowered = text.lower()
        total = 0.0
        for signal, weight in self.signals:
            if signal.lower() in lowered:
                total += weight
        return total


# 4 张路由卡片，按业务领域定义。
# 每张卡片包含：信号词（用于匹配）、优先工具、备用工具、给模型的指导语。
ROUTE_CARDS: tuple[RouteCard, ...] = (
    # 卡片 1：工件生成（PPT、图表、报告等）
    RouteCard(
        route="artifact_generation",
        reason="User asks for a chart, PPT, report, export, or other generated artifact.",
        preferred_tools=(
            "AutoChartGenerate",
            "AutoPptxGenerate",
            "SubjectsDataCatalogSearch",
            "SubjectsSqlSchema",
            "SubjectsSqlGlob",
            "SubjectsSqlQuery",
            "KnowledgeSearch",
            "KnowledgeFetch",
            "Read",
            "Glob",
        ),
        fallback_tools=("WebSearch", "WebFetch", "StructuredOutput", "SendUserMessage"),
        guidance=(
            "For artifact generation, first reuse current conversation evidence. If more data is needed, "
            "query SQL or KnowledgeSearch before using WebFetch. Generate the artifact only after the data is clear."
        ),
        signals=(
            ("ppt", 4.0),
            ("pptx", 4.0),
            ("幻灯片", 4.0),
            ("演示文稿", 4.0),
            ("生成图", 3.0),
            ("画图", 3.0),
            ("图表", 3.0),
            ("导出", 2.5),
            ("报告", 2.0),
            ("可视化", 2.0),
        ),
    ),
    # 卡片 2：车辆参数查询（车长、轴距、价格、配置等）
    RouteCard(
        route="vehicle_spec",
        reason="User asks for structured vehicle facts such as dimensions, configuration, price, range, or comparison.",
        preferred_tools=("SubjectsAttributeLookup", "SubjectsAttributeStats", "SubjectsDataCatalogSearch", "SubjectsSqlQuery"),
        fallback_tools=("SubjectsSqlSchema", "SubjectsSqlGlob", "KnowledgeSearch", "KnowledgeFetch", "WebSearch", "WebFetch", "StructuredOutput", "SendUserMessage"),
        guidance=(
            "For vehicle specification/configuration facts, treat the task as structured data discovery rather than recall. "
            "For direct entity-attribute questions, first call SubjectsAttributeLookup with broad entity and attribute keywords. "
            "If it returns matching rows, stop calling tools and answer from those rows. For statistics/aggregation over vehicle "
            "attributes, prefer SubjectsAttributeStats before raw SQL. Use SubjectsSqlSchema/SubjectsSqlGlob/SubjectsSqlQuery only when the lookup returns zero rows, the user asks for unsupported aggregation/comparison, or the requested "
            "attribute cannot be expressed as simple entity + attribute keywords. Use KnowledgeSearch or allowed AutoHome WebFetch "
            "only when SQL evidence is unavailable or clearly insufficient. Cite structured data evidence for numeric values."
        ),
        signals=(
            ("车长", 5.0),
            ("长度", 3.0),
            ("车宽", 3.0),
            ("车高", 3.0),
            ("长宽高", 4.0),
            ("轴距", 5.0),
            ("整备质量", 4.0),
            ("价格", 3.0),
            ("指导价", 4.0),
            ("报价", 3.0),
            ("续航", 4.0),
            ("电池", 2.0),
            ("参数", 3.0),
            ("配置", 3.0),
            ("尺寸", 4.0),
            ("对比", 2.0),
            ("竞品", 2.0),
            ("车身", 2.0),
        ),
    ),
    # 卡片 3：手册/说明书问答
    RouteCard(
        route="manual_qa",
        reason="User asks about manuals, feature usage, limitations, warnings, or original documents.",
        preferred_tools=("KnowledgeSearch", "KnowledgeFetch"),
        fallback_tools=("SubjectsSqlSchema", "SubjectsSqlQuery", "WebSearch", "WebFetch", "StructuredOutput", "SendUserMessage"),
        guidance=(
            "For manual/function explanations, first use KnowledgeSearch/KnowledgeFetch to ground claims in original documents. "
            "Use SQL only for structured vehicle metadata and allowed AutoHome WebFetch only when the knowledge base is insufficient."
        ),
        signals=(
            ("用户手册", 5.0),
            ("说明书", 5.0),
            ("使用限制", 4.0),
            ("使用条件", 4.0),
            ("故障灯", 3.0),
            ("警告灯", 3.0),
            ("功能说明", 3.0),
            ("原始文档", 4.0),
            ("文档链接", 3.0),
            ("遥控泊车", 5.0),
            ("泊车辅助", 4.0),
            ("adas", 2.5),
            ("noa", 2.5),
            ("acc", 2.5),
        ),
    ),
    # 卡片 4：趋势分析/市场调研
    RouteCard(
        route="trend_analysis",
        reason="User asks for research, trend analysis, market/technology interpretation, or competitive insight.",
        preferred_tools=("KnowledgeSearch", "KnowledgeFetch", "SubjectsAttributeLookup", "SubjectsAttributeStats", "SubjectsDataCatalogSearch", "SubjectsSqlQuery"),
        fallback_tools=("SubjectsSqlSchema", "SubjectsSqlGlob", "WebSearch", "WebFetch", "AutoChartGenerate", "AutoPptxGenerate", "StructuredOutput", "SendUserMessage"),
        guidance=(
            "For analysis tasks, combine evidence types: KnowledgeSearch for documents, SubjectsAttributeStats for governed vehicle attribute statistics, SQL for unsupported structured samples/statistics, "
            "and allowed AutoHome WebFetch only for missing external context. For governed vehicle configuration/cohort questions, "
            "prefer SubjectsAttributeLookup before raw SQL; use its optional filter_attribute_keyword/filter_value_keyword for cohorts "
            "such as level=MPV or body structure filters. Do not treat entity_type rows as vehicle segment/classification values; "
            "vehicle segment/class/body-style values live in entity_attribute + instance_attribute_value. Avoid relying on model common "
            "knowledge for factual claims."
        ),
        signals=(
            ("趋势", 4.0),
            ("市场", 3.0),
            ("调研", 3.5),
            ("分析", 3.0),
            ("洞察", 3.0),
            ("路线", 2.0),
            ("技术路线", 4.0),
            ("渗透率", 3.5),
            ("竞争", 3.0),
            ("竞品", 3.0),
            ("策略", 2.5),
            ("总结", 2.0),
        ),
    ),
)


def _route_for_user_request(user_text: str) -> RoutePolicy:
    """路由判断：遍历 4 张卡片，每张算分，得分最高的胜出。
    如果最高分 < 2.0（没有明显匹配），走通用路由（暴露全部工具）。
    这是一个基于关键词加权的简单分类器，不是神经网络。"""
    text = (user_text or "").strip()
    if not text:
        return GENERAL_ROUTE

    scored = sorted(
        ((card.score(text), index, card) for index, card in enumerate(ROUTE_CARDS)),
        key=lambda item: (item[0], -item[1]),
        reverse=True,
    )
    best_score, _, best_card = scored[0]
    if best_score < 2.0:
        return GENERAL_ROUTE
    return RoutePolicy(
        route=best_card.route,
        reason=best_card.reason,
        preferred_tools=best_card.preferred_tools,
        fallback_tools=best_card.fallback_tools,
        guidance=best_card.guidance,
    )


def _build_routed_tool_schemas(
    tool_registry: ToolRegistry,
    route_policy: RoutePolicy,
    tool_context: ToolContext | None = None,
    *,
    discovery_stage: str = "fallback",
    discovered_tool_names: set[str] | None = None,
) -> list[dict[str, Any]]:
    """根据路由策略过滤工具列表，只返回本轮应该暴露给模型的工具。
    
    两阶段发现机制：
      - primary 阶段：只暴露优先工具（route 匹配的领域）
      - fallback 阶段：展开到备用工具（优先工具不够用时）
    
    这样设计是为了避免模型一开始就看到 20 多个工具然后乱选。"""
    specs = (
        tool_registry.list_eligible_specs(tool_context)
        if tool_context is not None
        else tool_registry.list_specs()
    )
    if route_policy.route != "general":
        allowlist = (
            {name.lower() for name in route_policy.preferred_tools}
            if discovery_stage == "primary"
            else route_policy.tool_allowlist
        )
        # TodoWrite is a provider-neutral control primitive. It lets the model
        # own and revise a plan without hard-coding a domain workflow in Runtime.
        allowlist.add("todowrite")
        allowlist.update(name.lower() for name in (discovered_tool_names or set()))
        if discovery_stage != "primary":
            allowlist.add("toolsearch")
        filtered = [spec for spec in specs if spec.name.lower() in allowlist]
        if not filtered and discovery_stage == "primary":
            fallback_names = {name.lower() for name in route_policy.fallback_tools}
            filtered = [spec for spec in specs if spec.name.lower() in fallback_names]
        # Safety fallback: if custom registries in tests or user extensions do not
        # contain the routed tools, expose the original specs rather than breaking
        # the agent loop.
        if filtered:
            specs = filtered
    return [
        {
            "name": spec.name,
            "description": spec.description,
            "input_schema": spec.input_schema,
        }
        for spec in specs
    ]


def _filter_tool_schemas(tool_schemas: list[dict[str, Any]], names: set[str]) -> list[dict[str, Any]]:
    lowered = {name.lower() for name in names}
    return [schema for schema in tool_schemas if str(schema.get("name") or "").lower() in lowered]


def _completion_tool_names(
    requirement_state: TaskRequirementState,
    tool_registry: ToolRegistry,
    tool_context: ToolContext,
) -> set[str]:
    """Resolve open output obligations to eligible capabilities, not tool names.

    This is a generic completion gate: new artifact/output types participate by
    declaring matching capability output_modes or a matching namespace suffix.
    Runtime does not encode a task workflow or decide when research is complete.
    """
    targets: set[str] = set()
    for item in requirement_state.as_dict()["requirements"]:
        if item.get("status") != "open":
            continue
        requirement_id = str(item.get("id") or "")
        if requirement_id.startswith("artifact:"):
            targets.add(requirement_id.split(":", 1)[1].lower())
        elif requirement_id == "output:structured_json":
            targets.add("json")
    if not targets:
        return set()

    matches: set[str] = set()
    for spec in tool_registry.list_eligible_specs(tool_context):
        namespace_tail = str(spec.capability.namespace or "").lower().rsplit(".", 1)[-1]
        output_modes = {str(mode).lower() for mode in spec.capability.output_modes}
        if targets & ({namespace_tail} | output_modes):
            matches.add(spec.name)
    return matches


def _format_provider_tool_choice(
    provider: BaseProvider,
    mode: str,
    tool_names: set[str],
) -> Any | None:
    formatter = getattr(type(provider), "format_tool_choice", None)
    if not callable(formatter):
        return None
    if mode == "specific" and len(tool_names) == 1:
        return provider.format_tool_choice("specific", next(iter(tool_names)))
    return provider.format_tool_choice(mode)


def _model_override_for_tier(model_tier: str) -> str:
    tier = re.sub(r"[^A-Z0-9_]", "_", str(model_tier or "").strip().upper())
    if not tier or tier == "NONE":
        return ""
    return os.getenv(f"CLAWD_MODEL_TIER_{tier}_MODEL", "").strip()


def _should_stage_subjects_attribute_lookup(user_text: str, route_policy: RoutePolicy) -> bool:
    """判断是否先只暴露 SubjectsAttributeLookup 一个工具。
    简单问题（如「车长多少」）先查一步，查到就直接回答，不用暴露 SQL 等复杂工具。
    复杂问题（包含「对比」「分析」「统计」等信号）则直接暴露全部工具。"""
    """Use the one-step lookup first for simple entity+attribute requests.

    This is intentionally signal-based rather than tied to specific vehicle
    names or attribute values. Comparison, aggregation, and open-ended analysis
    still get the broader SQL/knowledge surface from the first turn.
    """
    if route_policy.route != "vehicle_spec":
        return False
    if "subjectsattributelookup" not in route_policy.tool_allowlist:
        return False
    text = (user_text or "").strip().lower()
    if not text:
        return False
    broad_task_signals = (
        "对比",
        "比较",
        "竞品",
        "分析",
        "调研",
        "趋势",
        "统计",
        "分布",
        "排名",
        "最高",
        "最低",
        "平均",
        "top",
        "所有",
        "哪些",
        "列表",
        "生成",
        "报告",
        "ppt",
        "图表",
    )
    return not any(signal in text for signal in broad_task_signals)


def _requires_model_owned_plan(user_text: str) -> bool:
    """判断是否需要模型先写一个执行计划再开始调工具。
    复杂任务（分析、汇总、对比、报告等）需要先规划。"""
    text = (user_text or "").strip().lower()
    if not text:
        return False
    complexity_signals = (
        "分析", "汇总", "总结", "对比", "比较", "趋势", "统计", "分布",
        "排名", "全部", "所有", "报告", "生成", "导出", "ppt", "图表",
        "analysis", "research", "compare", "all ", "report", "export",
    )
    return any(signal in text for signal in complexity_signals)


def _extract_requested_pptx_slide_titles(user_text: str, requested_count: int) -> list[str]:
    titles: list[str] = []
    for raw_line in (user_text or "").splitlines():
        line = raw_line.strip()
        match = re.match(r"^\s*(?:\d{1,2}|[一二三四五六七八九十]+)[\.、)]\s*(.+?)\s*$", line)
        if not match:
            continue
        title = match.group(1).strip()
        if title:
            titles.append(title)
    if len(titles) >= requested_count:
        return titles[:requested_count]

    default_titles = [
        "研究范围与数据边界",
        "核心配置维度",
        "前悬架配置分析",
        "后悬架配置分析",
        "分组对比结论",
        "后续补证清单",
    ]
    while len(titles) < requested_count:
        index = len(titles)
        fallback = default_titles[index] if index < len(default_titles) else f"报告页 {index + 1}"
        titles.append(fallback)
    return titles


def _build_coverage_limited_pptx_input(
    user_text: str,
    *,
    requested_count: int,
    file_suffix: str,
) -> dict[str, Any]:
    titles = _extract_requested_pptx_slide_titles(user_text, requested_count)
    source_footer = "当前受控知识库/数据目录检索结果：覆盖不足；需补充权威资料复核"
    slides: list[dict[str, Any]] = []
    for index, title in enumerate(titles, start=1):
        slides.append(
            {
                "title": title,
                "subtitle": "覆盖不足兜底报告页：先交付可下载文件，事实结论需后续补证",
                "key_points": [
                    "已按用户指定结构生成本页，不额外增加封面或目录页。",
                    "当前受控知识库未返回足够可引用资料，不能把空检索结果包装成确定事实。",
                    "本页保留调研维度和待核验字段，便于后续补充数据后直接替换。",
                ],
                "table": {
                    "columns": ["维度", "当前状态", "处理方式"],
                    "rows": [
                        ["研究对象", title, "按用户分组保留"],
                        ["证据覆盖", "不足", "需补充来源"],
                        ["交付状态", f"第 {index}/{requested_count} 页", "已生成"],
                    ],
                },
                "conclusion": "已生成可下载 PPTX；本页事实内容因资料覆盖不足需补证后定稿。",
                "notes": (
                    "Runtime fallback generated this slide because the requested artifact contract was open "
                    "and repeated research/catalog tools reported data coverage insufficient."
                ),
                "source_footer": source_footer,
            }
        )
    return {
        "requested_slide_count": requested_count,
        "deck_title": "资料覆盖受限调研报告",
        "file_name": f"coverage_limited_report_{file_suffix}",
        "slides": slides,
    }


def _configure_task_requirements(
    state: TaskRequirementState,
    route_policy: RoutePolicy,
    user_request: str,
) -> None:
    """根据路由设置任务要求：不同类型的任务需要什么类型的证据才算完成。
    比如车辆参数查询要求有 SQL 证据，手册问答要求有知识检索证据。"""
    if route_policy.route == "vehicle_spec":
        state.require_evidence(
            "evidence:structured_fact",
            "Ground structured vehicle facts in governed SQL, business-query, or catalog evidence.",
            evidence_kinds=("sql", "structured_data"),
        )
    elif route_policy.route == "manual_qa":
        state.require_evidence(
            "evidence:document_fact",
            "Ground manual or feature claims in retrieved document or fetched source evidence.",
            evidence_kinds=("knowledge", "web_fetch"),
        )
    elif route_policy.route == "trend_analysis":
        state.require_evidence(
            "evidence:research",
            "Ground the analysis in at least one governed SQL, document, or fetched-source evidence item.",
            evidence_kinds=("sql", "structured_data", "knowledge", "web_fetch"),
        )
    elif route_policy.route == "artifact_generation" and any(
        signal in (user_request or "").lower()
        for signal in ("调研", "分析", "对比", "配置", "趋势", "市场", "竞品", "research", "analysis", "compare")
    ):
        state.require_evidence(
            "evidence:artifact_content",
            "Ground factual artifact content in governed SQL, document, or fetched-source evidence.",
            evidence_kinds=("sql", "structured_data", "knowledge", "web_fetch"),
        )


def _format_route_policy(route_policy: RoutePolicy) -> str:
    """把路由策略格式化成文本，拼进系统提示词里，让模型知道该优先用什么工具。"""
    if route_policy.route == "general":
        return (
            "Harness routing policy:\n"
            f"- route: {route_policy.route}\n"
            f"- reason: {route_policy.reason}\n"
            f"- guidance: {route_policy.guidance}"
        )
    preferred = ", ".join(route_policy.preferred_tools) or "none"
    fallback = ", ".join(route_policy.fallback_tools) or "none"
    return (
        "Harness routing policy for this user request:\n"
        f"- route: {route_policy.route}\n"
        f"- reason: {route_policy.reason}\n"
        f"- preferred tools: {preferred}\n"
        f"- fallback tools: {fallback}\n"
        f"- guidance: {route_policy.guidance}\n"
        "- This is a source-priority policy, not a rigid script. If preferred tools are insufficient, use fallback tools and explain the data gap."
        "- For structured SQL tasks, confirm the join keys and enum literals from the database instead of guessing them."
    )


def _safe_call_handler(handler: ToolEventHandler | None, event: ToolEvent) -> None:
    """安全地调用事件回调。如果回调抛异常就吞掉，不影响主循环。"""
    if handler is None:
        return
    try:
        handler(event)
    except Exception:
        return


def _emit_text_chunks(handler: TextChunkHandler | None, text: str, *, chunk_size: int = 12) -> None:
    """把文本切成小块发给回调，用于流式显示（打字机效果）。"""
    if handler is None or not text:
        return
    if chunk_size <= 0:
        chunk_size = len(text)
    for idx in range(0, len(text), chunk_size):
        try:
            handler(text[idx: idx + chunk_size])
        except Exception:
            return


def _call_provider_for_turn(
    *,
    provider: BaseProvider,
    api_messages: list[dict[str, Any]],
    call_kwargs: dict[str, Any],
    stream: bool,
    on_text_chunk: TextChunkHandler | None,
) -> tuple[Any, bool]:
    """调用大模型，优先用流式，流式不行就降级到非流式。
    返回 (response, 是否流式输出了文本)。"""
    if stream:
        try:
            response = provider.chat_stream_response(
                api_messages,
                on_text_chunk=on_text_chunk,
                **call_kwargs,
            )
            if not isinstance(response, ChatResponse):
                raise TypeError("Structured streaming must return ChatResponse")
            return response, True
        except NotImplementedError:
            pass
        except Exception:
            # Preserve existing stable behavior if streaming is unsupported or fails.
            pass

    response = provider.chat(api_messages, **call_kwargs)
    return response, False


def _usage_or_none(total_usage: dict[str, int]) -> dict[str, int] | None:
    return total_usage if total_usage["input_tokens"] > 0 or total_usage["output_tokens"] > 0 else None


def _truncate_text(value: Any, limit: int = 900) -> str:
    text = str(value or "").replace("\r\n", "\n").strip()
    text = re.sub(r"\s+", " ", text)
    return text if len(text) <= limit else text[: max(0, limit - 3)] + "..."


# ==================== 证据与引用系统（L850-1080）====================
# 每次工具执行完，从结果里提取"证据"（SQL 行、知识块、网页内容），
# 分配编号 [1] [2] [3]...，然后附加到工具结果里给模型看。
# 最终回答必须引用这些编号，不能编造来源。

def _citation_key(item: dict[str, Any]) -> str:
    """生成证据的唯一标识，用于去重。优先用 chunk_id/url/source 等字段。"""
    for key in ("chunk_id", "url", "source", "source_ref", "query", "title"):
        value = item.get(key)
        if value:
            return f"{key}:{value}"
    return json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)[:500]


def _citation_kind(item: dict[str, Any]) -> str:
    source_type = str(item.get("source_type") or "").lower()
    tool_name = str(item.get("tool_name") or "").lower()
    if source_type == "sql" or tool_name == "subjectssqlquery":
        return "sql"
    if source_type == "knowledge" or tool_name in {"knowledgesearch", "knowledgefetch"}:
        return "knowledge"
    if source_type == "web_fetch" or tool_name == "webfetch":
        return "web_fetch"
    if source_type == "web_search" or tool_name == "websearch":
        return "web_search"
    return source_type or "other"


def _citation_kind_label(kind: str) -> str:
    return {
        "sql": "SQL",
        "knowledge": "Knowledge",
        "web_fetch": "WebFetch",
        "web_search": "WebSearch",
    }.get(kind, kind or "other")


def _citation_kind_rank(kind: str) -> int:
    return {
        "sql": 0,
        "knowledge": 1,
        "web_fetch": 2,
        "web_search": 3,
    }.get(kind, 9)


def _normalize_explicit_evidence(tool_name: str, item: Any, tool_input: dict[str, Any] | None = None) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    title = str(item.get("title") or item.get("name") or tool_name).strip()
    content = item.get("content") or item.get("excerpt") or item.get("snippet") or item.get("summary")
    source = item.get("source") or item.get("source_ref") or item.get("url") or item.get("path") or tool_name
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    if tool_input:
        metadata = {**metadata, "tool_input": tool_input}
    normalized = {
        "source_type": str(item.get("source_type") or item.get("type") or tool_name).strip(),
        "title": title,
        "source": str(source or "").strip(),
        "content": _truncate_text(content or title, 1200),
        "metadata": metadata,
    }
    for key in ("chunk_id", "document_id", "dataset_id", "page", "score", "query", "url"):
        if key in item and item[key] not in (None, ""):
            normalized[key] = item[key]
    return normalized


def _extract_evidence_candidates(tool_name: str, tool_input: dict[str, Any], output: Any) -> list[dict[str, Any]]:
    """从工具返回结果中提取"证据候选"。
    不同工具有不同的提取逻辑：
      - KnowledgeSearch → 提取 results 里的知识块
      - SubjectsSqlQuery → 提取 rows 里的 SQL 结果
      - WebSearch → 提取搜索结果
      - WebFetch → 提取抓取的网页内容
    不信任模型，只信任工具返回的原始数据。"""
    if not isinstance(output, dict):
        return []
    lowered = tool_name.lower()
    candidates: list[dict[str, Any]] = []

    explicit = output.get("evidence")
    if isinstance(explicit, list):
        for item in explicit:
            normalized = _normalize_explicit_evidence(tool_name, item, tool_input)
            if normalized is not None:
                candidates.append(normalized)

    if lowered in {"knowledgesearch", "knowledgefetch"}:
        for item in output.get("results") or []:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or item.get("document_name") or item.get("document_id") or "Knowledge chunk")
            source = str(item.get("source") or item.get("url") or item.get("document_id") or "")
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            candidates.append({
                "source_type": "knowledge",
                "title": title,
                "source": source,
                "content": _truncate_text(item.get("excerpt") or item.get("content") or item.get("text") or "", 1200),
                "metadata": {
                    **metadata,
                    "provider": output.get("provider"),
                    "dataset_id": item.get("dataset_id") or output.get("dataset_id"),
                    "document_id": item.get("document_id") or output.get("document_id"),
                    "chunk_id": item.get("chunk_id") or output.get("chunk_id"),
                    "score": item.get("score"),
                },
                "chunk_id": item.get("chunk_id") or output.get("chunk_id"),
                "document_id": item.get("document_id") or output.get("document_id"),
                "dataset_id": item.get("dataset_id") or output.get("dataset_id"),
                "score": item.get("score"),
            })

    elif lowered == "subjectssqlquery":
        rows = output.get("rows")
        if isinstance(rows, list) and rows:
            preview_rows = rows[:8]
            query = str(output.get("query") or tool_input.get("query") or "")
            candidates.append({
                "source_type": "sql",
                "title": "Subjects SQL query result",
                "source": "SubjectsSqlQuery",
                "content": _truncate_text(json.dumps(preview_rows, ensure_ascii=False, default=str), 1600),
                "metadata": {
                    "query": query,
                    "row_count": output.get("row_count"),
                    "truncated": output.get("truncated"),
                },
                "query": query,
            })

    elif lowered in {"subjectsattributelookup", "subjectsdatacatalogsearch"}:
        rows = output.get("rows") if lowered == "subjectsattributelookup" else output.get("matches")
        rows = rows if isinstance(rows, list) else []
        boundary = str(output.get("coverage_boundary") or "").strip()
        if rows or boundary:
            candidates.append({
                "source_type": "structured_data",
                "title": f"{tool_name} governed result",
                "source": tool_name,
                "content": _truncate_text(
                    json.dumps(rows[:8], ensure_ascii=False, default=str) if rows else boundary,
                    1600,
                ),
                "metadata": {
                    "row_count": output.get("row_count", output.get("match_count", len(rows))),
                    "coverage_boundary": boundary,
                    "tool_input": tool_input,
                },
            })

    elif isinstance(output.get("rows"), list) and output.get("rows"):
        rows = output["rows"]
        candidates.append({
            "source_type": "structured_data",
            "title": f"{tool_name} result",
            "source": tool_name,
            "content": _truncate_text(json.dumps(rows[:8], ensure_ascii=False, default=str), 1600),
            "metadata": {
                "row_count": output.get("row_count", len(rows)),
                "tool_input": tool_input,
            },
        })

    elif lowered == "websearch":
        for item in output.get("results") or []:
            if not isinstance(item, dict):
                continue
            candidates.append({
                "source_type": "web_search",
                "title": str(item.get("title") or item.get("url") or "Web search result"),
                "source": str(item.get("url") or ""),
                "url": item.get("url"),
                "content": _truncate_text(item.get("snippet") or item.get("title") or "", 800),
                "metadata": {"query": output.get("query")},
            })

    elif lowered == "webfetch" and output.get("url"):
        candidates.append({
            "source_type": "web_fetch",
            "title": str(output.get("url")),
            "source": str(output.get("url")),
            "url": output.get("url"),
            "content": _truncate_text(output.get("content") or "", 1600),
            "metadata": {"content_type": output.get("content_type")},
        })

    # Keep only candidates with usable content or source; de-dup later.
    return [
        item for item in candidates
        if str(item.get("content") or "").strip() or str(item.get("source") or "").strip()
    ]


def _format_citation_list(citations: list[dict[str, Any]], *, max_items: int = 16) -> str:
    """把证据列表格式化成引用清单，给模型看。
    格式如：
      [SQL evidence]
      [1] 标题 — 来源
          摘要：...
      [Knowledge evidence]
      [2] 标题 — 来源
          摘要：...
    最多 16 条，按类型分组。"""
    if not citations:
        return ""
    ordered = sorted(
        citations[-max_items:],
        key=lambda item: (_citation_kind_rank(_citation_kind(item)), int(item.get("citation_id") or 0)),
    )
    lines = [
        "可引用证据清单（由 Harness 分配编号；最终回答只能引用这些编号，不要编造来源）：",
        "使用规则：结构化事实、车型参数、数值、统计、字段名优先引用 SQL；手册说明优先引用 Knowledge；网页证据只做补充。",
    ]
    current_kind: str | None = None
    for item in ordered:
        kind = _citation_kind(item)
        if kind != current_kind:
            current_kind = kind
            lines.append(f"\n[{_citation_kind_label(kind)} evidence]")
        cid = item.get("citation_id")
        title = _truncate_text(item.get("title"), 120)
        source = _truncate_text(item.get("source"), 160)
        content = _truncate_text(item.get("content"), 420)
        meta = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        parts = []
        for key in ("chunk_id", "document_id", "query", "url"):
            value = item.get(key) or meta.get(key)
            if value:
                parts.append(f"{key}={_truncate_text(value, 120)}")
        suffix = f" ({'; '.join(parts[:3])})" if parts else ""
        lines.append(f"[{cid}] {title} — {source}{suffix}\n    摘要：{content}")
    return "\n".join(lines)


def _append_citation_note_to_tool_output(output: Any, new_citations: list[dict[str, Any]]) -> Any:
    """把新产生的引用列表附加到工具输出里，这样模型在下一轮能看到已有的引用。"""
    if not new_citations:
        return output
    note = _format_citation_list(new_citations)
    if isinstance(output, dict):
        return {**output, "citation_note": note, "citation_ids": [item["citation_id"] for item in new_citations]}
    return f"{output}\n\n{note}"


def _citation_ids_in_text(text: str) -> set[int]:
    """从文本中提取所有 [1] [2] [12] 这样的引用编号。"""
    found: set[int] = set()
    for match in re.finditer(r"\[(\d{1,3})\]", text or ""):
        try:
            found.add(int(match.group(1)))
        except ValueError:
            pass
    return found


def _extract_claims(text: str, citations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """从最终回答中提取"声明"（claims），并检查每个声明是否有引用支撑。
    比如回答里说「车长为 4980mm[1]」，就提取为一个 claim，
    检查 [1] 是否有效，以及 [1] 的内容是否包含 4980mm 这个数值。
    状态有：supported / weak_support / unsupported / invalid_citation / numeric_mismatch"""
    claims: list[dict[str, Any]] = []
    evidence_by_id = {int(item["citation_id"]): str(item.get("content") or "") for item in citations}
    valid_ids = set(evidence_by_id)
    parts = re.split(r"(?<=[。！？.!?])\s+|\n+", text or "")
    for part in parts:
        claim = re.sub(r"^[-*#\d.、\s]+", "", part).strip()
        if len(claim) < 8 or claim.startswith("证据来源"):
            continue
        citation_ids = sorted(_citation_ids_in_text(claim))
        factual = bool(re.search(r"\d|为|是|采用|达到|支持|相比|高于|低于|来源", claim))
        if not factual and not citation_ids:
            continue
        invalid = [item for item in citation_ids if item not in valid_ids]
        if invalid:
            status = "invalid_citation"
        elif citation_ids:
            evidence_text = " ".join(evidence_by_id[item] for item in citation_ids)
            status = _claim_support_status(claim, evidence_text)
        else:
            status = "unsupported"
        claims.append({
            "claim_id": f"claim_{len(claims) + 1}",
            "text": claim[:1000],
            "citation_ids": citation_ids,
            "status": status,
        })
    return claims


def _claim_support_status(claim: str, evidence_text: str) -> str:
    """检查一个声明是否被证据支撑。
    先比对数值（声明里的数字是否都在证据里出现过），
    再比对文本重叠度（中文用 bigram，英文用单词）。
    重叠度 >= 12% 算 supported，否则 weak_support。"""
    claim_numbers = set(re.findall(r"\d+(?:\.\d+)?(?:%|mm|kg|km|kWh|V|A)?", claim, re.IGNORECASE))
    evidence_numbers = set(re.findall(r"\d+(?:\.\d+)?(?:%|mm|kg|km|kWh|V|A)?", evidence_text, re.IGNORECASE))
    if claim_numbers and not claim_numbers.issubset(evidence_numbers):
        return "numeric_mismatch"
    claim_tokens = _support_tokens(claim)
    evidence_tokens = _support_tokens(evidence_text)
    if not claim_tokens:
        return "supported"
    overlap = len(claim_tokens & evidence_tokens) / len(claim_tokens)
    return "supported" if overlap >= 0.12 else "weak_support"


def _support_tokens(text: str) -> set[str]:
    normalized = re.sub(r"\[\d{1,3}\]", "", text.lower())
    latin = set(re.findall(r"[a-z][a-z0-9_.-]{1,}", normalized))
    cjk = "".join(re.findall(r"[\u4e00-\u9fff]", normalized))
    bigrams = {cjk[index:index + 2] for index in range(max(0, len(cjk) - 1))}
    return latin | bigrams


def _replace_last_assistant_text(conversation: Conversation, text: str, metadata: dict[str, Any] | None = None) -> None:
    """替换对话里最后一条 assistant 消息的内容。用于引用修复后更新最终回答。"""
    for message in reversed(conversation.messages):
        if message.role != "assistant":
            continue
        message.content = text
        if metadata:
            message.metadata.update(metadata)
        return
    conversation.add_assistant_message(text, metadata=metadata)


def _canonical_url(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    try:
        parts = urlsplit(value.strip())
    except Exception:
        return value.strip()
    path = parts.path.rstrip("/") or parts.path
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, parts.query, ""))


def _build_effective_system_prompt(style_prompt: str, tool_context: ToolContext) -> str:
    try:
        context_prompt = build_context_prompt(
            tool_context.workspace_root,
            cwd=tool_context.cwd,
        )
    except Exception:
        context_prompt = ""
    if not context_prompt.strip():
        return style_prompt
    return f"{style_prompt}\n\n{context_prompt}"


def summarize_tool_use(name: str, tool_input: dict[str, Any]) -> str:
    """生成工具调用的简短摘要，用于终端日志打印。"""
    lowered = name.lower()
    if lowered == "bash":
        cmd = tool_input.get("command")
        if isinstance(cmd, str):
            s = cmd.strip().replace("\n", " ")
            return s if len(s) <= 80 else s[:77] + "..."
        return ""
    if lowered in {"read", "write", "edit"}:
        p = tool_input.get("file_path") or tool_input.get("filePath") or tool_input.get("path")
        if isinstance(p, str):
            extra = ""
            if lowered == "read":
                off = tool_input.get("offset")
                lim = tool_input.get("limit")
                if isinstance(off, int) or isinstance(lim, int):
                    start = off if isinstance(off, int) else 1
                    if isinstance(lim, int):
                        extra = f" · lines {start}-{start + lim - 1}"
            return f"{p}{extra}"
        return ""
    if lowered == "glob":
        pat = tool_input.get("pattern")
        base = tool_input.get("path")
        if isinstance(pat, str) and isinstance(base, str):
            return f"{pat} · {base}"
        if isinstance(pat, str):
            return pat
        return ""
    if lowered == "grep":
        pat = tool_input.get("pattern")
        base = tool_input.get("path")
        if isinstance(pat, str) and isinstance(base, str):
            return f"{pat} · {base}"
        if isinstance(pat, str):
            return pat
        return ""
    if lowered == "webfetch":
        url = tool_input.get("url")
        return url if isinstance(url, str) else ""
    if lowered == "websearch":
        q = tool_input.get("query")
        return q if isinstance(q, str) else ""
    if lowered == "toolsearch":
        q = tool_input.get("query")
        return q if isinstance(q, str) else ""
    if lowered == "askuserquestion":
        qs = tool_input.get("questions")
        if isinstance(qs, list):
            return f"{len(qs)} question(s)"
        return ""
    if lowered == "sendusermessage":
        status = tool_input.get("status")
        return status if isinstance(status, str) else ""
    return ""



def run_agent_loop(
    conversation: Conversation,      # 对话历史（包含用户最新消息）
    provider: BaseProvider,           # 大模型提供者（Anthropic/OpenAI/Minimax）
    tool_registry: ToolRegistry,      # 工具注册表（所有可用工具）
    tool_context: ToolContext,        # 工具上下文（权限、工作目录、任务 ID 等）
    max_turns: int = 12,              # 最大工具调用轮次
    stream: bool = False,             # 是否流式输出
    verbose: bool = False,            # 是否打印调试信息
    on_event: ToolEventHandler | None = None,    # 事件回调（给前端发 SSE）
    on_text_chunk: TextChunkHandler | None = None, # 文本块回调（流式显示）
) -> AgentLoopResult:
    """Agent 主循环：大模型 → 工具 → 大模型 → ... → 最终回答。

    核心逻辑：
      1. 路由判断（决定暴露哪些工具）
      2. 拼装系统提示词
      3. 进入循环：调模型 → 处理工具调用 → 安全检查 → 下一轮
      4. 循环结束：引用修复 + 声明校验 + 组装最终结果
    """
    # ==================== 初始化阶段 ====================
    # 1. 路由决策：判断用户问题属于哪个领域
    user_request = _last_user_text(conversation)  # 取最后一条用户消息
    route_decision = decide_route(user_request, allow_deterministic=False)
    route_policy = RoutePolicy(
        route=route_decision.route,
        reason=", ".join(route_decision.reason_codes),
        preferred_tools=route_decision.preferred_tools,
        fallback_tools=route_decision.fallback_tools,
        guidance=(
            f"RouteDecision: execution_path={route_decision.execution_path}, "
            f"model_tier={route_decision.model_tier}, budget_class={route_decision.budget_class}, "
            f"tool_profile={route_decision.tool_profile}. "
            "For vehicle specification facts, treat the task as structured data discovery; "
            "for direct entity-attribute questions, first call SubjectsAttributeLookup."
        ),
    )
    # 2. 任务要求：什么条件满足才算完成
    stage_subjects_lookup = _should_stage_subjects_attribute_lookup(user_request, route_policy)
    requirement_state = TaskRequirementState.from_user_request(user_request)
    _configure_task_requirements(requirement_state, route_policy, user_request)  # 不同路由要求不同类型的证据

    # 3. 运行状态：跟踪执行进度
    tool_context.runtime_state["output_contract"] = requirement_state.output_contract
    run_state = AgentRunState.from_requirements(
        user_request,
        requirement_state.as_dict()["requirements"],
    )
    tool_context.runtime_state["agent_run_state"] = run_state.as_dict()
    run_budget = RunBudget.from_env()  # 运行预算（token 上限、工具调用上限等）
    tool_context.runtime_state["run_budget"] = run_budget.as_dict()

    # 4. 工具发现：两阶段（primary → fallback）
    discovery_stage = "primary" if route_policy.route != "general" else "fallback"
    discovery_expansion_reasons: list[str] = []
    discovered_tool_names: set[str] = set()
    last_exposed_tool_names: tuple[str, ...] | None = None

    # 5. 拼装系统提示词 = 基础提示 + 路由策略 + 引用策略 + 执行策略 + 任务要求
    openai_messages: list[dict[str, Any]] = []  # OpenAI 格式的消息列表（非 Anthropic 模型用）
    last_user_visible_message: str | None = None
    style_name = getattr(tool_context, "output_style_name", None)
    style_dir = getattr(tool_context, "output_style_dir", None)
    style_prompt = resolve_output_style(style_name, style_dir).prompt
    effective_system_prompt = _build_effective_system_prompt(style_prompt, tool_context)
    effective_system_prompt = f"{effective_system_prompt}\n\n{_format_route_policy(route_policy)}"
    citation_policy = (
        "Citation policy for evidence-grounded answers:\n"
        "- When tool results include a Harness-provided citation_note / citation_ids, cite factual claims with those exact bracket ids, e.g. [1].\n"
        "- Do not invent citation ids, document names, URLs, SQL rows, or page numbers.\n"
        "- If the evidence list contains SQL citations and the answer is about structured facts, vehicle parameters, counts, or statistics, prefer SQL citations over knowledge chunks.\n"
        "- For structured questions, if SQL evidence contains the requested numeric/value rows, answer with those values instead of saying the data is unavailable.\n"
        "- If evidence is insufficient, state the limitation explicitly instead of guessing.\n"
        "- Put a short '证据来源' section at the end when citations are used."
    )
    effective_system_prompt = f"{effective_system_prompt}\n\n{citation_policy}"
    execution_policy = (
        "Tool execution policy:\n"
        "- Use the smallest sufficient set of tool calls. Plan the evidence needed before calling tools.\n"
        "- For a multi-step task, use TodoWrite to create and maintain your own concise execution plan. "
        "Update it when a step completes or when evidence shows the current path is low-yield. "
        "For the in-progress step, optional toolHints may name exact tools that should form the next candidate set.\n"
        "- Prefer one well-targeted call over several exploratory variants, and do not request schema discovery when a higher-level tool already returned usable rows.\n"
        "- After a result directly answers the question, stop calling tools and synthesize the answer.\n"
        "- Treat each model turn as one dependency layer. Before asking for another model turn, issue all currently ready, "
        "independent, necessary tool calls together; the Runtime will execute eligible read-only calls concurrently. "
        "When a result completes a plan step, update TodoWrite and issue the independent tools for newly ready steps in the "
        "same response instead of spending a separate turn only reporting plan status. Do not batch calls when one needs "
        "another call's result, and do not include speculative work."
    )
    effective_system_prompt = f"{effective_system_prompt}\n\n{execution_policy}"
    effective_system_prompt = f"{effective_system_prompt}\n\n{requirement_state.prompt()}"

    # Seed OpenAI messages from initial conversation messages
    for msg in conversation.messages:
        if isinstance(msg.content, str):
            openai_messages.append({"role": msg.role, "content": msg.content})
        else:
            # If there are already block messages, we are probably Anthropic; leave as is
            pass

    # 6. 安全熔断器：防止死循环和资源耗尽
    total_usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0}  # token 累计
    turn_count = 0              # 模型调用次数
    execution_fuse = max(1, int(os.getenv("CLAWD_TOOL_EXECUTION_FUSE", "24")))  # 工具调用总上限
    tool_call_count = 0         # 已执行的工具调用次数
    tool_call_fingerprints: set[str] = set()     # 工具调用指纹（去重用）
    disabled_tool_names: set[str] = set()        # 本轮被禁用的工具
    result_fingerprints: set[str] = set()        # 工具结果指纹（检测重复结果）
    consecutive_no_progress = 0  # 连续无进展次数（≥3 触发强制合成）
    citations: list[dict[str, Any]] = []  # 所有收集到的证据
    # 紧急保险丝：最多 24 次工具调用，防止死循环
    websearch_failures = 0
    blocked_websearch_attempts = 0
    fetched_urls: set[str] = set()
    successful_webfetches = 0
    failed_webfetches = 0
    subjects_lookup_satisfied = False
    tool_failures_by_name: dict[str, int] = {}
    coverage_insufficient_results = 0
    execution_fuse = max(
        1,
        int(
            os.getenv(
                "CLAWD_TOOL_EXECUTION_FUSE",
                os.getenv("CLAWD_MAX_TOOL_CALLS_PER_RUN", "24"),
            )
        ),
    )
    tool_call_count = 0
    tool_call_fingerprints: set[str] = set()
    preflight_rejection_fingerprints: set[str] = set()
    preflight_rejections: dict[tuple[str, str], int] = {}
    disabled_tool_names: set[str] = set()
    result_fingerprints: set[str] = set()
    duplicate_tool_calls = 0
    duplicate_tool_results = 0
    consecutive_no_progress = 0
    citations: list[dict[str, Any]] = []
    citation_keys: set[str] = set()
    contract_reminder_count = 0
    requirement_items = requirement_state.as_dict()["requirements"]
    selected_model_override = _model_override_for_tier(route_decision.model_tier)
    plan_required = bool(
        requirement_state.output_contract.required
        or len(requirement_items) > 1
        or _requires_model_owned_plan(user_request)
    )
    plan_checkpoint_required = plan_required

    # ==================== 内部函数定义 ====================
    # 这些函数在 run_agent_loop 内部定义，可以访问外层的所有变量

    def register_evidence(tool_name: str, tool_input: dict[str, Any], result_output: Any) -> list[dict[str, Any]]:
        """从工具结果中提取证据，分配编号 [1] [2]...，去重后存入 citations 列表。"""
        new_items: list[dict[str, Any]] = []
        for candidate in _extract_evidence_candidates(tool_name, tool_input, result_output):
            key = _citation_key(candidate)
            if key in citation_keys:
                continue
            citation_keys.add(key)
            item = {
                **candidate,
                "citation_id": len(citations) + 1,
                "tool_name": tool_name,
            }
            item["evidence_hash"] = hashlib.sha256(
                json.dumps(item, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
            ).hexdigest()
            citations.append(item)
            new_items.append(item)
        return new_items

    def repair_citations_if_needed(final_text: str) -> str:
        """引用修复：检查模型回答是否用了正确的引用编号。
        如果没引用或引用错误，再调一次模型让它修正。
        如果 SQL 有数值但回答里没写数值，让模型重写。"""
        nonlocal turn_count
        if not citations or not final_text.strip():
            return final_text
        used = _citation_ids_in_text(final_text)
        valid_ids = {int(item["citation_id"]) for item in citations}
        sql_ids = {
            int(item["citation_id"])
            for item in citations
            if _citation_kind(item) == "sql"
        }
        sql_has_numeric_rows = any(
            any(ch.isdigit() for ch in str(item.get("content") or ""))
            for item in citations
            if _citation_kind(item) == "sql"
        )
        needs_structured_value_rewrite = (
            route_policy.route in {"vehicle_spec", "trend_analysis", "artifact_generation"}
            and bool(sql_ids)
            and sql_has_numeric_rows
            and (
                not any(ch.isdigit() for ch in final_text)
                or any(flag in final_text for flag in ("无法确认", "未包含", "缺少", "查阅", "需要补充"))
            )
        )
        require_sql_citation = route_policy.route in {"vehicle_spec", "trend_analysis", "artifact_generation"} and bool(sql_ids)
        if used and used.issubset(valid_ids) and (not require_sql_citation or bool(used & sql_ids)) and not needs_structured_value_rewrite:
            return final_text
        citation_list = _format_citation_list(citations)
        rewrite_hint = ""
        if needs_structured_value_rewrite:
            rewrite_hint = (
                "The SQL evidence includes explicit numeric/value rows for this structured question. "
                "Rewrite the answer so it states the actual values from the SQL evidence. Do not claim the data is unavailable "
                "unless the evidence truly lacks it."
            )
        repair_instruction = (
            "Rewrite the draft answer to be evidence-grounded. Keep the same substantive answer, "
            "but add citations using only the provided bracket ids. Do not add new facts. "
            "Every factual data claim, comparison, source-backed statement, or numeric value should cite at least one id. "
            "If there are SQL citations in the evidence list and the answer discusses structured facts, numeric values, "
            "vehicle parameters, counts, or statistics, prefer at least one SQL citation such as [11]. "
            f"{rewrite_hint} "
            "If a claim has no supporting evidence in the list, mark it as uncertain or remove it.\n\n"
            f"{citation_list}\n\n"
            f"Draft answer:\n{final_text}"
        )
        try:
            turn_count += 1
            if _is_anthropic_provider(provider):
                repair_messages = [
                    *conversation.get_messages(),
                    {"role": "user", "content": repair_instruction},
                ]
                repair_response = provider.chat(repair_messages, tools=None, system=effective_system_prompt)
            else:
                repair_messages = [
                    {"role": "system", "content": effective_system_prompt},
                    *openai_messages,
                    {"role": "user", "content": repair_instruction},
                ]
                repair_response = provider.chat(repair_messages, tools=None)
            if repair_response.usage:
                total_usage["input_tokens"] += repair_response.usage.get("input_tokens", 0)
                total_usage["output_tokens"] += repair_response.usage.get("output_tokens", 0)
            repaired = repair_response.content or final_text
            repaired_ids = _citation_ids_in_text(repaired)
            if repaired.strip() and repaired_ids and repaired_ids.issubset(valid_ids):
                return repaired
        except Exception:
            return final_text
        return final_text

    def finalize_text(final_text: str, *, metadata: dict[str, Any] | None = None, already_added: bool = True) -> AgentLoopResult:
        """最终处理：引用修复 → 声明校验 → 组装元数据 → 返回结果。
        这是所有退出路径的汇聚点，不管怎么退出都走这里。"""
        repaired = repair_citations_if_needed(final_text)
        final_metadata = dict(metadata or {})
        claims = _extract_claims(repaired, citations)
        evidence_status = "supported"
        if any(item["status"] in {"unsupported", "invalid_citation", "numeric_mismatch", "weak_support"} for item in claims):
            evidence_status = "needs_review"
        elif not claims:
            evidence_status = "not_applicable"
        high_risk_claims = [
            item for item in claims
            if item["status"] in {"unsupported", "invalid_citation", "numeric_mismatch"}
        ]
        evidence_review_notice = "证据校验提示：部分事实或数值未通过自动证据一致性检查，请在工程决策前人工复核。"
        if high_risk_claims and evidence_review_notice not in repaired:
            repaired = (
                repaired.rstrip()
                + f"\n\n> {evidence_review_notice}"
            )
        if citations:
            final_metadata["citations"] = citations
        if claims:
            final_metadata["claims"] = claims
        final_metadata["evidence_status"] = evidence_status
        contract_payload = requirement_state.as_dict()
        final_metadata["task_contract"] = contract_payload
        final_metadata["output_contract_status"] = requirement_state.output_contract_status
        final_metadata["task_contract_status"] = requirement_state.status
        final_metadata["run_state"] = run_state.as_dict()
        scheduler_ledger = tool_context.runtime_state.get("tool_scheduler_ledger")
        if isinstance(scheduler_ledger, dict):
            final_metadata["tool_scheduler_ledger"] = scheduler_ledger
        final_metadata["run_budget"] = run_budget.as_dict()
        final_metadata["route_decision"] = route_decision.as_dict()
        final_metadata["budget_class"] = route_decision.budget_class
        final_metadata["model_tier"] = route_decision.model_tier
        final_metadata["tool_profile"] = route_decision.tool_profile
        final_metadata["model_routing"] = {
            "requested_tier": route_decision.model_tier,
            "model_override": selected_model_override,
            "provider_default_model": getattr(provider, "model", None),
        }
        termination_reason = str(
            final_metadata.get("termination_reason")
            or ("completed" if requirement_state.is_satisfied else "incomplete_contract")
        )
        final_metadata["termination_reason"] = termination_reason
        tool_context.runtime_state["agent_run_state"] = run_state.as_dict()
        if repaired != final_text or not already_added:
            _replace_last_assistant_text(conversation, repaired, metadata=final_metadata)
        elif final_metadata:
            _replace_last_assistant_text(conversation, final_text, metadata=final_metadata)
        if stream:
            _emit_text_chunks(on_text_chunk, repaired)
        return AgentLoopResult(
            response_text=repaired,
            usage=_usage_or_none(total_usage),
            num_turns=turn_count,
            citations=list(citations) or None,
            claims=claims or None,
            evidence_status=evidence_status,
            route=route_policy.route,
            route_policy_version=route_decision.route_policy_version,
            output_contract_status=requirement_state.output_contract_status,
            task_contract_status=requirement_state.status,
            requirements=contract_payload["requirements"],
            termination_reason=termination_reason,
            run_state=run_state.as_dict(),
            tool_scheduler_ledger=scheduler_ledger if isinstance(scheduler_ledger, dict) else None,
            run_budget=run_budget.as_dict(),
            route_decision=route_decision.as_dict(),
            model_tier=route_decision.model_tier,
            budget_class=route_decision.budget_class,
            model_routing=final_metadata["model_routing"],
        )

    def finalize_blocked(reason: str) -> AgentLoopResult:
        unmet = requirement_state.unmet_requirements()
        details = "；".join(item.description for item in unmet) or "任务要求尚未满足"
        text = f"任务未完成：{details}。Runtime 终止原因：{reason}。"
        return finalize_text(
            text,
            metadata={"mode": "runtime_blocked", "termination_reason": reason},
            already_added=False,
        )

    def try_generate_coverage_limited_pptx(reason: str) -> AgentLoopResult | None:
        nonlocal tool_call_count
        if route_policy.route != "artifact_generation":
            return None
        if requirement_state.output_contract_status == "satisfied":
            return None
        ppt_requirement = next(
            (
                item
                for item in requirement_state.output_contract.required_artifacts
                if item.artifact_type == "pptx"
            ),
            None,
        )
        if ppt_requirement is None:
            return None
        ppt_tool = tool_registry.get("AutoPptxGenerate")
        if ppt_tool is None:
            return None
        requested_count = int(ppt_requirement.exact_count or 6)
        if requested_count < 1 or requested_count > 20:
            return None

        suffix_source = tool_context.job_id or tool_context.trace_id or str(int(time.time()))
        suffix = re.sub(r"[^a-zA-Z0-9_-]+", "_", suffix_source).strip("_")[:48] or "agent"
        tool_input = _build_coverage_limited_pptx_input(
            user_request,
            requested_count=requested_count,
            file_suffix=suffix,
        )
        call = ToolCall(
            name="AutoPptxGenerate",
            input=tool_input,
            tool_use_id=f"runtime_pptx_fallback_{tool_call_count + 1}",
        )
        preflight = tool_registry.preflight(call, tool_context)
        if not preflight.can_execute:
            tool_context.audit_events.append(
                {
                    "event": "coverage_limited_pptx_fallback_rejected",
                    "job_id": tool_context.job_id,
                    "trace_id": tool_context.trace_id,
                    "reason": reason,
                    "reason_code": preflight.reason_code,
                    "message": preflight.message,
                }
            )
            return None

        _safe_call_handler(
            on_event,
            ToolEvent(kind="tool_use", tool_name=call.name, tool_input=call.input, tool_use_id=call.tool_use_id),
        )
        tool_call_count += 1
        result = tool_registry.dispatch(call, tool_context)
        requirement_state.update_from_tool_result(
            call.name,
            result,
            tool_context,
            capability_namespace=ppt_tool.spec().capability.namespace,
            output_modes=ppt_tool.spec().capability.output_modes,
        )
        artifact_satisfied = requirement_state.output_contract_status == "satisfied"
        run_state.record_action(
            tool_name=call.name,
            tool_input=call.input,
            outcome_status=result.outcome_status.value,
            reason_code=result.reason_code,
            requirement_changes=["artifact:pptx"] if artifact_satisfied else [],
            new_evidence_count=0,
            result_is_novel=True,
            plan_progress=False,
        )
        tool_context.runtime_state["agent_run_state"] = run_state.as_dict()
        _safe_call_handler(
            on_event,
            ToolEvent(
                kind="tool_result",
                tool_name=call.name,
                tool_output=result.output,
                tool_use_id=call.tool_use_id,
                is_error=result.is_error,
                outcome_status=result.outcome_status.value,
                reason_code=result.reason_code,
                retryable=result.retryable,
            ),
        )
        if result.is_error or not isinstance(result.output, dict):
            return None

        file_path = str(result.output.get("file_path") or "")
        url = str(result.output.get("url") or "")
        final_lines = [
            "已生成可下载的 PowerPoint 文件。",
            "",
            f"- 文件：{file_path}",
        ]
        if url:
            final_lines.append(f"- 下载地址：{url}")
        final_lines.extend(
            [
                f"- 页数：{result.output.get('slide_count')}/{requested_count}",
                "",
                "说明：本次检索中受控知识库/数据目录连续返回覆盖不足，因此已生成“资料覆盖受限版”报告。PPT 保留用户要求的页面结构和待核验字段，避免把无证据内容写成确定事实。",
            ]
        )
        return finalize_text(
            "\n".join(final_lines),
            metadata={
                "termination_reason": "coverage_limited_artifact_generated",
                "coverage_limited": True,
                "coverage_limited_reason": reason,
                "artifact_generation_mode": "deterministic_fallback",
            },
            already_added=False,
        )

    def should_stop_low_yield_tools() -> str:
        """检查是否应该停止调工具，强制从已有证据合成答案。
        停止条件：
          - 工具调用达到上限（24 次）
          - 模型重复同样的调用 ≥ 2 次
          - 工具结果重复 ≥ 2 次
          - 连续 3 次没进展
          - 8 轮以上且失败 ≥ 6 次"""
        total_failures = sum(tool_failures_by_name.values())
        if tool_call_count >= execution_fuse:
            return f"emergency tool execution fuse reached ({execution_fuse})"
        if duplicate_tool_calls >= 2:
            return "the model repeated equivalent tool calls"
        if duplicate_tool_results >= 2:
            return "multiple tool calls returned evidence already gathered"
        if consecutive_no_progress >= 3:
            return "three consecutive eligible tool executions produced no new evidence or requirement progress"
        if turn_count >= 8 and total_failures >= 6:
            return "the current tool path is low-yield after repeated failures"
        return ""

    def record_tool_failure(tool_name: str) -> None:
        """记录某个工具失败了一次。"""
        lowered = tool_name.lower()
        tool_failures_by_name[lowered] = tool_failures_by_name.get(lowered, 0) + 1

    def synthesize_from_evidence(reason: str) -> AgentLoopResult | None:
        nonlocal turn_count
        synthesis_instruction = (
            "Stop using tools now. Synthesize the best possible final answer from the evidence already gathered. "
            f"Reason for stopping tool use: {reason}. "
            "If the evidence is incomplete or conflicting, say so explicitly. Use only Harness-provided citation ids "
            "such as [1] when citing evidence, and avoid inventing missing facts.\n\n"
            f"{_format_citation_list(citations)}"
        )
        try:
            turn_count += 1
            evidence_snapshot = json.dumps(
                run_state.evidence_ledger[-20:],
                ensure_ascii=False,
                default=str,
            )
            compact_instruction = (
                f"Original user request:\n{user_request}\n\n"
                f"Durable evidence ledger:\n{evidence_snapshot}\n\n"
                f"{synthesis_instruction}"
            )
            if _is_anthropic_provider(provider):
                synth_messages = [{"role": "user", "content": compact_instruction}]
                synth_response = provider.chat(synth_messages, tools=None, system=effective_system_prompt)
            else:
                synth_messages = [
                    {"role": "system", "content": effective_system_prompt},
                    {"role": "user", "content": compact_instruction},
                ]
                synth_response = provider.chat(synth_messages, tools=None)
            if synth_response.usage:
                total_usage["input_tokens"] += synth_response.usage.get("input_tokens", 0)
                total_usage["output_tokens"] += synth_response.usage.get("output_tokens", 0)
            final_text = synth_response.content or last_user_visible_message or ""
            if not final_text.strip():
                tool_context.audit_events.append({
                    "event": "evidence_synthesis_failed",
                    "job_id": tool_context.job_id,
                    "trace_id": tool_context.trace_id,
                    "reason": reason,
                    "failure": "empty_model_response",
                })
                return None
            if stream:
                # finalize_text emits after optional citation repair.
                pass
            conversation.add_assistant_message(final_text, metadata={"mode": "tool_loop_synthesis", "reason": reason})
            return finalize_text(final_text, metadata={"mode": "tool_loop_synthesis", "reason": reason}, already_added=True)
        except Exception as exc:
            tool_context.audit_events.append({
                "event": "evidence_synthesis_failed",
                "job_id": tool_context.job_id,
                "trace_id": tool_context.trace_id,
                "reason": reason,
                "failure": type(exc).__name__,
                "message": str(exc)[:500],
            })
            return None

    # ==================== 主循环开始 ====================
    # 每一轮：构建工具列表 → 调大模型 → 处理工具调用 → 检查停止条件
    for turn in range(max_turns):
        if tool_context.is_cancelled():
            raise AgentRunCancelled("agent run cancelled")

        # ---------- 第 1 步：构建本轮工具列表 ----------
        # 根据路由策略过滤工具，可能只暴露一部分工具给模型
        turn_system_prompt = f"{effective_system_prompt}\n\n{run_state.prompt()}"
        discovered_tool_names.update(run_state.active_tool_hints)
        if _is_anthropic_provider(provider):
            api_messages = conversation.get_messages()
        else:
            # Use OpenAI formatted messages for non-Anthropic
            api_messages = openai_messages

        tool_schemas = _build_routed_tool_schemas(
            tool_registry,
            route_policy,
            tool_context,
            discovery_stage=discovery_stage,
            discovered_tool_names=discovered_tool_names,
        )
        if tool_context.allowed_tools:
            tool_schemas = _filter_tool_schemas(tool_schemas, set(tool_context.allowed_tools))
        active_tool_schemas = [
            schema
            for schema in tool_schemas
            if str(schema.get("name") or "").lower() not in disabled_tool_names
        ]
        if subjects_lookup_satisfied:
            active_tool_schemas = []
        elif stage_subjects_lookup and turn == 0:
            staged = _filter_tool_schemas(tool_schemas, {"SubjectsAttributeLookup"})
            if staged:
                active_tool_schemas = staged

        eligible_tool_schemas = list(active_tool_schemas)

        planning_checkpoint = bool(
            plan_required
            and not run_state.has_plan
            and not requirement_state.is_satisfied
            and not subjects_lookup_satisfied
        )
        if planning_checkpoint:
            planning_tools = _filter_tool_schemas(eligible_tool_schemas, {"TodoWrite"})
            if planning_tools:
                active_tool_schemas = planning_tools

        # The plan is model-authored. When its active step names exact tool
        # hints, honor them as dynamic candidate narrowing after eligibility and
        # authorization checks. Keep route-preferred tools visible so a weak
        # model-authored plan cannot hide higher-level deterministic tools and
        # force lower-level SQL by accident. Invalid hints simply fall back to
        # normal routing.
        active_hints = run_state.active_tool_hints
        if active_hints and not subjects_lookup_satisfied and not plan_checkpoint_required:
            protected_route_tools = set(route_policy.preferred_tools) if route_policy.route != "general" else set()
            hinted = _filter_tool_schemas(active_tool_schemas, active_hints | protected_route_tools | {"TodoWrite", "ToolSearch"})
            hinted_actions = [
                item for item in hinted
                if str(item.get("name") or "").lower() not in {"todowrite", "toolsearch"}
            ]
            if hinted_actions:
                active_tool_schemas = hinted

        completion_names = _completion_tool_names(requirement_state, tool_registry, tool_context)
        completion_recovery = bool(completion_names) and (
            contract_reminder_count > 0 or turn == max_turns - 1
        )
        if completion_recovery:
            # Completion recovery must be able to escape stale plan hints or a
            # pending plan checkpoint. Eligibility and authorization still come
            # from the same per-turn candidate set.
            narrowed = _filter_tool_schemas(eligible_tool_schemas, completion_names)
            if narrowed:
                active_tool_schemas = narrowed

        exposed_tool_names = tuple(str(schema.get("name") or "") for schema in active_tool_schemas)
        if exposed_tool_names != last_exposed_tool_names:
            tool_context.audit_events.append(
                {
                    "event": "tool_candidates_exposed",
                    "job_id": tool_context.job_id,
                    "trace_id": tool_context.trace_id,
                    "route": route_policy.route,
                    "discovery_stage": (
                        "completion_recovery"
                        if completion_recovery
                        else "planning_checkpoint"
                        if planning_checkpoint
                        else "post_action_review"
                        if plan_required and plan_checkpoint_required and run_state.has_plan
                        else discovery_stage
                    ),
                    "tool_names": list(exposed_tool_names),
                    "candidate_count": len(exposed_tool_names),
                    "expansion_reasons": list(discovery_expansion_reasons),
                }
            )
            last_exposed_tool_names = exposed_tool_names

        call_kwargs: dict[str, Any] = {"tools": active_tool_schemas}
        if selected_model_override:
            call_kwargs["model"] = selected_model_override
        if completion_recovery:
            choice_mode = "specific" if len(active_tool_schemas) == 1 else "required"
            tool_choice = _format_provider_tool_choice(
                provider,
                choice_mode,
                {str(item.get("name") or "") for item in active_tool_schemas},
            )
            if tool_choice is not None:
                call_kwargs["tool_choice"] = tool_choice
            tool_context.audit_events.append(
                {
                    "event": "completion_recovery_activated",
                    "job_id": tool_context.job_id,
                    "trace_id": tool_context.trace_id,
                    "tool_names": [str(item.get("name") or "") for item in active_tool_schemas],
                    "tool_choice_enforced": tool_choice is not None,
                    "reason": "model_attempted_completion" if contract_reminder_count > 0 else "last_safe_turn",
                }
            )
        elif planning_checkpoint and active_tool_schemas:
            tool_choice = _format_provider_tool_choice(provider, "specific", {"TodoWrite"})
            if tool_choice is not None:
                call_kwargs["tool_choice"] = tool_choice
            tool_context.audit_events.append(
                {
                    "event": "planning_checkpoint_activated",
                    "job_id": tool_context.job_id,
                    "trace_id": tool_context.trace_id,
                    "reason": "initial_plan" if not run_state.has_plan else "post_action_review",
                    "tool_choice_enforced": tool_choice is not None,
                }
            )
        if _is_anthropic_provider(provider):
            call_kwargs["system"] = turn_system_prompt
        else:
            # System policy and live run state must be present on every request.
            # Previously non-Anthropic providers received them only on turn 0,
            # leaving later tool choices without the route or task contract.
            api_messages = [{"role": "system", "content": turn_system_prompt}, *api_messages]
        budget_result = prepare_messages_with_budget(
            api_messages,
            system_prompt=turn_system_prompt if _is_anthropic_provider(provider) else "",
            tool_schemas=active_tool_schemas,
        )
        api_messages = budget_result.messages
        if budget_result.compacted:
            tool_context.audit_events.append({
                "event": "context_compacted",
                "job_id": tool_context.job_id,
                "trace_id": tool_context.trace_id,
                "before_tokens": budget_result.before_tokens,
                "after_tokens": budget_result.after_tokens,
                "dropped_atomic_units": budget_result.dropped_units,
                "hard_limit_reached": budget_result.hard_limit_reached,
            })
        # ---------- 第 2 步：调用大模型 ----------
        # 把对话历史 + 可用工具列表交给模型，模型返回文本和/或工具调用
        turn_text_chunks: list[str] = []
        response, streamed_live_text = _call_provider_for_turn(
            provider=provider,
            api_messages=api_messages,
            call_kwargs=call_kwargs,
            stream=stream,
            on_text_chunk=turn_text_chunks.append if stream else None,
        )
        turn_count += 1
        if tool_context.is_cancelled():
            raise AgentRunCancelled("agent run cancelled")

        # Collect usage info
        if response.usage:
            total_usage["input_tokens"] += response.usage.get("input_tokens", 0)
            total_usage["output_tokens"] += response.usage.get("output_tokens", 0)
        run_budget.record_model_turn(response.usage)
        tool_context.runtime_state["run_budget"] = run_budget.as_dict()

        # ---------- 第 3 步：处理模型返回 ----------
        # 统计 token 用量
        final_assistant_content = response.content or ""  # 模型的文本输出
        tool_uses = response.tool_uses or []              # 模型要求的工具调用列表
        normalized_tool_uses: list[dict[str, Any]] = []
        for tool_use in tool_uses:
            raw_call = ToolCall(
                name=str(tool_use.get("name") or ""),
                input=tool_use.get("input") if isinstance(tool_use.get("input"), dict) else {},
                tool_use_id=str(tool_use.get("id") or ""),
            )
            normalized_call, applied_aliases = tool_registry.normalize_call(raw_call)
            if applied_aliases:
                tool_context.audit_events.append(
                    {
                        "event": "tool_input_normalized",
                        "job_id": tool_context.job_id,
                        "trace_id": tool_context.trace_id,
                        "tool_name": normalized_call.name,
                        "aliases": dict(applied_aliases),
                    }
                )
            normalized_tool_uses.append(
                {
                    **tool_use,
                    "id": normalized_call.tool_use_id,
                    "name": normalized_call.name,
                    "input": normalized_call.input,
                }
            )
        tool_uses = normalized_tool_uses
        response.tool_uses = tool_uses or None

        if _is_anthropic_provider(provider):
            assistant_blocks: list = []
            if response.content:
                assistant_blocks.append(TextContentBlock(type="text", text=response.content))

            for tool_use in tool_uses:
                assistant_blocks.append(ToolUseContentBlock(
                    type="tool_use",
                    id=tool_use["id"],
                    name=tool_use["name"],
                    input=tool_use["input"],
                ))

            conversation.add_assistant_message(assistant_blocks if assistant_blocks else "")
        else:
            # Add assistant message to OpenAI messages (text only)
            openai_assistant_msg: dict[str, Any] = {"role": "assistant", "content": final_assistant_content}
            # If there are tool_uses, add them in OpenAI format
            if response.tool_uses:
                # Build OpenAI tool_calls
                tool_calls = []
                for tu in response.tool_uses:
                    tool_calls.append({
                        "id": tu["id"],
                        "type": "function",
                        "function": {
                            "name": tu["name"],
                            "arguments": json.dumps(tu["input"], ensure_ascii=False)
                        }
                    })
                openai_assistant_msg["tool_calls"] = tool_calls
            openai_messages.append(openai_assistant_msg)
            if not tool_uses:
                # Persist only user-visible final assistant text. Intermediate
                # planning text that accompanies tool calls belongs in telemetry,
                # not in chat history.
                conversation.add_assistant_message(final_assistant_content)

        # ---------- 第 4 步：模型没调工具 → 检查是否完成 ----------
        if not tool_uses:
            if not requirement_state.is_satisfied:
                if discovery_stage == "primary" and route_policy.fallback_tools:
                    discovery_stage = "fallback"
                    discovery_expansion_reasons.append("output_contract_unmet")
                contract_reminder_count += 1
                reminder = requirement_state.reminder()
                tool_context.audit_events.append(
                    {
                        "event": "output_contract_unmet",
                        "job_id": tool_context.job_id,
                        "trace_id": tool_context.trace_id,
                        "reminder_count": contract_reminder_count,
                        "requirements": requirement_state.as_dict()["requirements"],
                    }
                )
                if not active_tool_schemas:
                    if requirement_state.output_contract.required:
                        return finalize_blocked("no_eligible_tool_for_open_output_contract")
                    synthesized = synthesize_from_evidence(
                        "the required output contract is unmet and no eligible tools remain"
                    )
                    if synthesized is not None:
                        return synthesized
                if _is_anthropic_provider(provider):
                    conversation.add_user_message(reminder)
                else:
                    openai_messages.append({"role": "user", "content": reminder})
                continue
            # 任务完成，退出循环
            # No more tools, done
            if (final_assistant_content or "").strip() == "" and last_user_visible_message is not None:
                return finalize_text(last_user_visible_message, already_added=False)
            return finalize_text(final_assistant_content, already_added=True)

        # A model turn is one dependency layer: sibling tool calls cannot consume
        # each other's outputs, so calls explicitly marked read-only + parallel
        # are safe to execute together. Results are still committed below in the
        # model's original order so conversation and run-state remain deterministic.
        # ---------- 第 5 步：并行执行检查 ----------
        # 如果模型同时调了多个工具，检查是否可以并行执行
        # 条件：全部只读 + 幂等 + 无副作用 + 不重复
        parallel_results: dict[str, Any] = {}
        parallel_dispatched_ids: set[str] = set()
        if len(tool_uses) > 1:
            parallel_calls: list[tuple[dict[str, Any], ToolCall]] = []
            batch_fingerprints: set[str] = set()
            batch_is_safe = tool_call_count + len(tool_uses) <= execution_fuse
            for tool_use in tool_uses:
                tool_id = str(tool_use.get("id") or "")
                tool_name = str(tool_use.get("name") or "")
                tool_input = tool_use.get("input")
                candidate_tool = tool_registry.get(tool_name)
                candidate_spec = candidate_tool.spec() if candidate_tool is not None else None
                fingerprint = _stable_fingerprint({"name": tool_name.lower(), "input": tool_input})
                call = ToolCall(name=tool_name, input=tool_input, tool_use_id=tool_id)
                preflight = tool_registry.preflight(call, tool_context)
                is_plan_control = bool(
                    candidate_spec
                    and candidate_spec.capability.namespace == "agent.plan"
                )
                if is_plan_control:
                    # TodoWrite may accompany the next ready dependency layer in
                    # the same model response. It is committed in model order
                    # below and does not prevent sibling read-only calls from
                    # running concurrently.
                    continue
                safe_call = bool(
                    tool_id
                    and isinstance(tool_input, dict)
                    and candidate_spec
                    and candidate_spec.is_read_only
                    and candidate_spec.execution.supports_parallel
                    and candidate_spec.execution.side_effect == "none"
                    and candidate_spec.execution.idempotent
                    and preflight.can_execute
                    and tool_name.lower() not in disabled_tool_names
                    and tool_failures_by_name.get(tool_name.lower(), 0) < 2
                    and fingerprint not in tool_call_fingerprints
                    and fingerprint not in batch_fingerprints
                )
                if tool_name.lower() == "webfetch":
                    url_key = _canonical_url(tool_input.get("url")) if isinstance(tool_input, dict) else ""
                    safe_call = safe_call and (not url_key or url_key not in fetched_urls)
                if tool_name.lower() == "websearch":
                    safe_call = safe_call and websearch_failures < 2
                if not safe_call:
                    batch_is_safe = False
                    break
                batch_fingerprints.add(fingerprint)
                parallel_calls.append((tool_use, call))

            if batch_is_safe and len(parallel_calls) > 1:
                parallel_dispatched_ids = {call.tool_use_id or "" for _, call in parallel_calls}
                for tool_use, call in parallel_calls:
                    _safe_call_handler(
                        on_event,
                        ToolEvent(
                            kind="tool_use",
                            tool_name=call.name,
                            tool_input=call.input,
                            tool_use_id=call.tool_use_id,
                        ),
                    )
                scheduled_results = ToolCallScheduler(tool_registry, tool_context).execute(
                    [call for _, call in parallel_calls],
                    mode="agent_loop",
                    allow_parallel=True,
                    max_workers=_max_parallel_tools(),
                )
                run_budget.record_scheduler_ledger(tool_context.runtime_state.get("tool_scheduler_ledger"))
                tool_context.runtime_state["run_budget"] = run_budget.as_dict()
                for scheduled in scheduled_results:
                    parallel_results[scheduled.call.tool_use_id or ""] = scheduled.result

        # ---------- 第 6 步：逐个处理工具调用 ----------
        # 对每个工具调用做一整套安全检查，然后执行
        force_synthesis_reason = ""
        for tool_use in tool_uses:
            if tool_context.is_cancelled():
                raise AgentRunCancelled("agent run cancelled")
            tool_id = tool_use["id"]
            tool_name = tool_use["name"]
            tool_input = tool_use["input"]

            try:
                # ===== 安全检查流水线 =====
                # 工具调用前必须通过以下所有检查，否则直接返回错误给模型

                # 检查 1：Preflight（权限、依赖健康、数据范围）
                call = ToolCall(name=tool_name, input=tool_input, tool_use_id=tool_id)
                preflight = tool_registry.preflight(call, tool_context)
                approval_can_continue = (
                    preflight.status == EligibilityStatus.NEEDS_APPROVAL
                    and tool_context.permission_handler is not None
                )
                if not preflight.can_execute and not approval_can_continue:
                    preflight_outcome = preflight_outcome_status(preflight)
                    if (
                        discovery_stage == "primary"
                        and preflight_outcome in {
                            ToolOutcomeStatus.CAPABILITY_MISMATCH,
                            ToolOutcomeStatus.DATA_COVERAGE_INSUFFICIENT,
                            ToolOutcomeStatus.DEPENDENCY_UNHEALTHY,
                            ToolOutcomeStatus.PERMISSION_DENIED,
                        }
                    ):
                        discovery_stage = "fallback"
                        discovery_expansion_reasons.append(preflight.reason_code)
                    rejection_fingerprint = _stable_fingerprint(
                        {
                            "name": str(tool_name).lower(),
                            "input": tool_input,
                            "reason_code": preflight.reason_code,
                        }
                    )
                    repeated_rejection = rejection_fingerprint in preflight_rejection_fingerprints
                    preflight_rejection_fingerprints.add(rejection_fingerprint)
                    rejection_key = (str(tool_name).lower(), preflight.reason_code)
                    preflight_rejections[rejection_key] = preflight_rejections.get(rejection_key, 0) + 1
                    if preflight.disable_tool_for_run or preflight_rejections[rejection_key] >= 2:
                        disabled_tool_names.add(str(tool_name).lower())
                    rejection_output = {
                        "error": preflight.message,
                        "preflight_rejected": True,
                        "reason_code": preflight.reason_code,
                        "retryable": preflight.retryable,
                        "tool_removed_for_run": str(tool_name).lower() in disabled_tool_names,
                        "repeated_rejection": repeated_rejection,
                        "alternative_capabilities": list(preflight.alternative_capabilities),
                    }
                    tool_context.audit_events.append(
                        {
                            "event": "tool_preflight_rejected",
                            "job_id": tool_context.job_id,
                            "trace_id": tool_context.trace_id,
                            "tool_name": tool_name,
                            "reason_code": preflight.reason_code,
                            "retryable": preflight.retryable,
                            "tool_removed_for_run": str(tool_name).lower() in disabled_tool_names,
                        }
                    )
                    _safe_call_handler(
                        on_event,
                        ToolEvent(
                            kind="tool_result",
                            tool_name=tool_name,
                            tool_output=rejection_output,
                            tool_use_id=tool_id,
                            is_error=True,
                            outcome_status=preflight_outcome.value,
                            reason_code=preflight.reason_code,
                            retryable=preflight.retryable,
                        ),
                    )
                    if _is_anthropic_provider(provider):
                        conversation.add_tool_result_message(tool_id, rejection_output, is_error=True)
                    else:
                        openai_messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tool_id,
                                "content": _build_openai_tool_result_content(rejection_output),
                            }
                        )
                    continue

                # 检查 2：工具是否已被禁用？
                tool_fingerprint = _stable_fingerprint(
                    {"name": str(tool_name).lower(), "input": tool_input}
                )
                if str(tool_name).lower() in disabled_tool_names and tool_id not in parallel_dispatched_ids:
                    error_str = (
                        f"Error: {tool_name} was disabled for this run after a non-recoverable capability, "
                        "permission, or dependency boundary. Choose a different capability."
                    )
                    _safe_call_handler(
                        on_event,
                        ToolEvent(
                            kind="tool_error",
                            tool_name=tool_name,
                            tool_input=tool_input,
                            tool_use_id=tool_id,
                            is_error=True,
                            error=error_str,
                        ),
                    )
                    if _is_anthropic_provider(provider):
                        conversation.add_tool_result_message(tool_id, error_str, is_error=True)
                    else:
                        openai_messages.append({"role": "tool", "tool_call_id": tool_id, "content": error_str})
                    continue
                candidate_tool = tool_registry.get(tool_name)
                candidate_spec = candidate_tool.spec() if candidate_tool is not None else None
                is_control_action = bool(
                    candidate_spec
                    and candidate_spec.capability.namespace in {"agent.plan"}
                )
                # 检查 3：重复调用检测 + 熔断器检查
                duplicate_external_action = (
                    not is_control_action and tool_fingerprint in tool_call_fingerprints
                )
                if (not is_control_action and tool_call_count >= execution_fuse) or duplicate_external_action:
                    if duplicate_external_action:
                        duplicate_tool_calls += 1
                        error_str = (
                            "Error: an equivalent tool call already ran in this task. "
                            "Use the existing result or choose a materially different query."
                        )
                        force_synthesis_reason = should_stop_low_yield_tools() or force_synthesis_reason
                    else:
                        error_str = (
                            f"Error: the emergency tool execution fuse ({execution_fuse}) was reached. "
                            "Stop execution and report the current evidence and unmet requirements."
                        )
                        force_synthesis_reason = f"emergency tool execution fuse reached ({execution_fuse})"
                    _safe_call_handler(
                        on_event,
                        ToolEvent(
                            kind="tool_error",
                            tool_name=tool_name,
                            tool_input=tool_input,
                            tool_use_id=tool_id,
                            is_error=True,
                            error=error_str,
                        ),
                    )
                    if _is_anthropic_provider(provider):
                        conversation.add_tool_result_message(tool_id, error_str, is_error=True)
                    else:
                        openai_messages.append({
                            "role": "tool",
                            "tool_call_id": tool_id,
                            "content": error_str,
                        })
                    continue
                if (
                    tool_id not in parallel_dispatched_ids
                    and tool_failures_by_name.get(tool_name.lower(), 0) >= 2
                    and tool_name.lower() not in {"webfetch", "websearch"}
                ):
                    error_str = (
                        f"Error: {tool_name} is unavailable after repeated failures in this task. "
                        "Use another evidence source or synthesize from current evidence instead of retrying this tool."
                    )
                    record_tool_failure(tool_name)
                    _safe_call_handler(
                        on_event,
                        ToolEvent(
                            kind="tool_error",
                            tool_name=tool_name,
                            tool_input=tool_input,
                            tool_use_id=tool_id,
                            is_error=True,
                            error=error_str,
                        ),
                    )
                    if _is_anthropic_provider(provider):
                        conversation.add_tool_result_message(tool_id, error_str, is_error=True)
                    else:
                        openai_messages.append({
                            "role": "tool",
                            "tool_call_id": tool_id,
                            "content": error_str
                        })
                    continue
                if tool_name.lower() == "webfetch" and tool_id not in parallel_dispatched_ids:
                    url_key = _canonical_url(tool_input.get("url") if isinstance(tool_input, dict) else "")
                    if url_key and url_key in fetched_urls:
                        failed_webfetches += 1
                        record_tool_failure(tool_name)
                        error_str = (
                            "Error: this URL has already been fetched in this task. "
                            "Do not fetch duplicate URLs; synthesize from existing evidence or choose a clearly different authoritative source."
                        )
                        _safe_call_handler(
                            on_event,
                            ToolEvent(
                                kind="tool_error",
                                tool_name=tool_name,
                                tool_input=tool_input,
                                tool_use_id=tool_id,
                                is_error=True,
                                error=error_str,
                            ),
                        )
                        if _is_anthropic_provider(provider):
                            conversation.add_tool_result_message(tool_id, error_str, is_error=True)
                        else:
                            openai_messages.append({
                                "role": "tool",
                                "tool_call_id": tool_id,
                                "content": error_str
                            })
                        low_yield_reason = should_stop_low_yield_tools()
                        if low_yield_reason:
                            synthesized = synthesize_from_evidence(low_yield_reason)
                            if synthesized is not None:
                                return synthesized
                        continue

                if (
                    tool_name.lower() == "websearch"
                    and websearch_failures >= 2
                    and tool_id not in parallel_dispatched_ids
                ):
                    blocked_websearch_attempts += 1
                    record_tool_failure(tool_name)
                    error_str = (
                        "Error: WebSearch is temporarily unavailable after repeated network failures. "
                        "Do not call WebSearch again for this task; use WebFetch on authoritative URLs "
                        "already known from context or synthesize from gathered evidence."
                    )
                    _safe_call_handler(
                        on_event,
                        ToolEvent(
                            kind="tool_error",
                            tool_name=tool_name,
                            tool_input=tool_input,
                            tool_use_id=tool_id,
                            is_error=True,
                            error=error_str,
                        ),
                    )
                    if _is_anthropic_provider(provider):
                        conversation.add_tool_result_message(tool_id, error_str, is_error=True)
                    else:
                        openai_messages.append({
                            "role": "tool",
                            "tool_call_id": tool_id,
                            "content": error_str
                        })
                    if blocked_websearch_attempts >= 2:
                        synthesized = synthesize_from_evidence("WebSearch repeated network failures")
                        if synthesized is not None:
                            return synthesized
                    continue

                # ✅ 通过所有检查，执行工具
                # 只有真正要执行的调用才计入熔断器
                if not is_control_action:
                    tool_call_fingerprints.add(tool_fingerprint)
                    tool_call_count += 1
                if tool_id not in parallel_dispatched_ids:
                    _safe_call_handler(
                        on_event,
                        ToolEvent(
                            kind="tool_use",
                            tool_name=tool_name,
                            tool_input=tool_input,
                            tool_use_id=tool_id,
                        ),
                    )
                # 执行工具调用
                requirement_statuses_before = {
                    item["id"]: item["status"]
                    for item in requirement_state.as_dict()["requirements"]
                }
                if tool_id in parallel_dispatched_ids:
                    result = parallel_results[tool_id]
                    if isinstance(result, Exception):
                        raise result
                else:
                    result = tool_registry.dispatch(call, tool_context)
                # ===== 执行后处理 =====
                result_output = result.output

                # 统计覆盖不足次数（≥4 次触发 PPT 兜底）
                if result.outcome_status == ToolOutcomeStatus.DATA_COVERAGE_INSUFFICIENT:
                    coverage_insufficient_results += 1
                # 工具返回“能力不匹配/依赖不健康”→ 禁用该工具
                if _result_disables_tool_for_run(result):
                    disabled_tool_names.add(str(tool_name).lower())
                # 取消检查
                if result.outcome_status == ToolOutcomeStatus.CANCELLED or tool_context.is_cancelled():
                    raise AgentRunCancelled("agent run cancelled")

                # 更新任务要求状态（工具结果是否满足了某个要求）
                dispatched_tool = tool_registry.get(tool_name)
                dispatched_spec = dispatched_tool.spec() if dispatched_tool is not None else None
                requirement_state.update_from_tool_result(
                    tool_name,
                    result,
                    tool_context,
                    capability_namespace=(dispatched_spec.capability.namespace if dispatched_spec else ""),
                    output_modes=(dispatched_spec.capability.output_modes if dispatched_spec else ()),
                )
                # 工具发现扩展：primary 阶段失败 → 展开到 fallback
                if tool_name.lower() == "toolsearch" and not result.is_error and isinstance(result.output, dict):
                    matches = result.output.get("matches")
                    if isinstance(matches, list):
                        discovered_tool_names.update(str(name) for name in matches if str(name).strip())
                if (
                    discovery_stage == "primary"
                    and result.outcome_status in {
                        ToolOutcomeStatus.NO_DATA,
                        ToolOutcomeStatus.CAPABILITY_MISMATCH,
                        ToolOutcomeStatus.DATA_COVERAGE_INSUFFICIENT,
                        ToolOutcomeStatus.DEPENDENCY_UNHEALTHY,
                        ToolOutcomeStatus.PERMISSION_DENIED,
                    }
                ):
                    discovery_stage = "fallback"
                    discovery_expansion_reasons.append(result.reason_code or result.outcome_status.value)
                    tool_context.audit_events.append(
                        {
                            "event": "tool_discovery_expanded",
                            "job_id": tool_context.job_id,
                            "trace_id": tool_context.trace_id,
                            "route": route_policy.route,
                            "reason": discovery_expansion_reasons[-1],
                            "from_stage": "primary",
                            "to_stage": "fallback",
                        }
                    )
                # 结果去重：指纹比对，检测是否返回了已经见过的结果
                result_fingerprint = _stable_fingerprint(result_output)
                result_is_novel = result_fingerprint not in result_fingerprints
                if not result_is_novel:
                    duplicate_tool_results += 1
                    force_synthesis_reason = should_stop_low_yield_tools() or force_synthesis_reason
                else:
                    result_fingerprints.add(result_fingerprint)
                # 注册证据 + 更新任务状态
                new_citations = [] if result.is_error else register_evidence(tool_name, tool_input, result_output)
                run_state.add_evidence(new_citations)
                requirement_state.update_from_evidence(new_citations)
                requirement_statuses_after = {
                    item["id"]: item["status"]
                    for item in requirement_state.as_dict()["requirements"]
                }
                requirement_changes = [
                    requirement_id
                    for requirement_id, status in requirement_statuses_after.items()
                    if status != requirement_statuses_before.get(requirement_id)
                ]
                plan_progress = False
                if tool_name.lower() == "todowrite" and isinstance(result_output, dict):
                    plan_progress = run_state.update_plan(result_output.get("newTodos"))
                    if run_state.has_plan:
                        requirement_state.update_plan_completion(
                            has_plan=True,
                            plan_complete=run_state.plan_complete,
                        )
                        plan_checkpoint_required = False
                requirement_progress = bool(requirement_changes)
                run_state.record_action(
                    tool_name=tool_name,
                    tool_input=tool_input,
                    outcome_status=result.outcome_status.value,
                    reason_code=result.reason_code,
                    requirement_changes=requirement_changes,
                    new_evidence_count=len(new_citations),
                    result_is_novel=result_is_novel,
                    plan_progress=plan_progress,
                )
                if (
                    plan_required
                    and tool_name.lower() != "todowrite"
                    and not result.is_error
                    and (result_is_novel or bool(new_citations) or requirement_progress)
                ):
                    plan_checkpoint_required = True
                tool_context.runtime_state["agent_run_state"] = run_state.as_dict()
                tool_context.audit_events.append(
                    {
                        "event": "agent_run_state_updated",
                        "job_id": tool_context.job_id,
                        "trace_id": tool_context.trace_id,
                        "tool_name": tool_name,
                        "requirement_changes": requirement_changes,
                        "plan_revision": run_state.plan_revision,
                        "actions_without_goal_progress": run_state.consecutive_without_goal_progress,
                    }
                )
                made_progress = requirement_progress or (
                    not result.is_error
                    and result.outcome_status not in {
                        ToolOutcomeStatus.NO_DATA,
                        ToolOutcomeStatus.CAPABILITY_MISMATCH,
                        ToolOutcomeStatus.DATA_COVERAGE_INSUFFICIENT,
                        ToolOutcomeStatus.PERMISSION_DENIED,
                        ToolOutcomeStatus.APPROVAL_REQUIRED,
                        ToolOutcomeStatus.DEPENDENCY_UNHEALTHY,
                        ToolOutcomeStatus.TRANSIENT_FAILURE,
                        ToolOutcomeStatus.PERMANENT_FAILURE,
                        ToolOutcomeStatus.CANCELLED,
                        ToolOutcomeStatus.TIMEOUT,
                    }
                    and (result_is_novel or bool(new_citations))
                )
                run_budget.record_scheduler_ledger(tool_context.runtime_state.get("tool_scheduler_ledger"))
                run_budget.record_tool_result(result, made_progress=made_progress)
                tool_context.runtime_state["run_budget"] = run_budget.as_dict()
                degrade_budget, budget_reason = run_budget.should_degrade()
                if degrade_budget:
                    tool_context.audit_events.append(
                        {
                            "event": "run_budget_degrade_recommended",
                            "job_id": tool_context.job_id,
                            "trace_id": tool_context.trace_id,
                            "reason": budget_reason,
                            "run_budget": run_budget.as_dict(),
                        }
                    )
                consecutive_no_progress = 0 if made_progress else consecutive_no_progress + 1
                progress_stop_reason = should_stop_low_yield_tools()
                if degrade_budget and budget_reason:
                    progress_stop_reason = progress_stop_reason or budget_reason
                if progress_stop_reason and not requirement_state.output_contract.required:
                    force_synthesis_reason = progress_stop_reason
                observation_output = _append_citation_note_to_tool_output(result_output, new_citations)
                model_observation_output = _compact_model_observation(tool_name, result, observation_output)
                if tool_name.lower() == "websearch":
                    if result.is_error:
                        websearch_failures += 1
                        record_tool_failure(tool_name)
                    elif isinstance(result_output, dict) and isinstance(result_output.get("results"), list) and not result_output.get("results"):
                        websearch_failures += 1
                        record_tool_failure(tool_name)
                    else:
                        websearch_failures = 0
                        blocked_websearch_attempts = 0
                elif not result.is_error:
                    blocked_websearch_attempts = 0
                if tool_name.lower() == "webfetch":
                    url_key = _canonical_url(tool_input.get("url") if isinstance(tool_input, dict) else "")
                    if url_key:
                        fetched_urls.add(url_key)
                    if result.is_error:
                        failed_webfetches += 1
                        record_tool_failure(tool_name)
                    else:
                        successful_webfetches += 1
                        blocked_websearch_attempts = 0
                elif result.is_error and _result_indicates_tool_health_failure(result):
                    record_tool_failure(tool_name)
                if tool_name.lower() == "sendusermessage" and isinstance(result_output, dict):
                    msg = result_output.get("message")
                    if isinstance(msg, str):
                        last_user_visible_message = msg
                if tool_name.lower() == "structuredoutput" and isinstance(result_output, dict):
                    payload = result_output.get("structured_output")
                    try:
                        last_user_visible_message = json.dumps(payload, ensure_ascii=False, indent=2)
                    except Exception:
                        last_user_visible_message = str(payload)
                if (
                    tool_name.lower() == "subjectsattributelookup"
                    and not result.is_error
                    and isinstance(result_output, dict)
                ):
                    row_count = int(result_output.get("row_count") or 0)
                    attribute_candidates = result_output.get("attribute_candidates")
                    filtered_entity_count = result_output.get("filtered_entity_count")
                    has_governed_boundary = (
                        row_count > 0
                        or (
                            bool(attribute_candidates)
                            and (
                                filtered_entity_count is not None
                                or result.reason_code == "VEHICLE_ATTRIBUTE_NO_ROWS"
                            )
                        )
                    )
                    if has_governed_boundary:
                        subjects_lookup_satisfied = True

                if verbose:
                    use_summary = summarize_tool_use(tool_name, tool_input)
                    if use_summary:
                        print(f"{tool_name} · {use_summary}")
                    summary = summarize_tool_result(tool_name, result_output)
                    print(f"{summary}")

                _safe_call_handler(
                    on_event,
                    ToolEvent(
                        kind="tool_result",
                        tool_name=tool_name,
                        tool_output=observation_output,
                        tool_use_id=tool_id,
                        is_error=result.is_error,
                        outcome_status=result.outcome_status.value,
                        reason_code=result.reason_code,
                        retryable=result.retryable,
                    ),
                )
                if _is_anthropic_provider(provider):
                    conversation.add_tool_result_message(tool_id, model_observation_output)
                else:
                    # Add tool result in OpenAI format
                    openai_messages.append({
                        "role": "tool",
                        "tool_call_id": tool_id,
                        "content": _build_openai_tool_result_content(model_observation_output)
                    })
            except Exception as e:
                error_str = f"Error: {e}"
                record_tool_failure(tool_name)
                if tool_name.lower() == "websearch":
                    websearch_failures += 1
                if tool_name.lower() == "webfetch":
                    failed_webfetches += 1
                    url_key = _canonical_url(tool_input.get("url") if isinstance(tool_input, dict) else "")
                    if url_key:
                        fetched_urls.add(url_key)
                if verbose:
                    print(f"[Tool Error] {error_str}")
                _safe_call_handler(
                    on_event,
                    ToolEvent(
                        kind="tool_error",
                        tool_name=tool_name,
                        tool_input=tool_input,
                        tool_use_id=tool_id,
                        is_error=True,
                        error=error_str,
                    ),
                )
                if _is_anthropic_provider(provider):
                    conversation.add_tool_result_message(tool_id, error_str, is_error=True)
                else:
                    openai_messages.append({
                        "role": "tool",
                        "tool_call_id": tool_id,
                        "content": error_str
                    })

        # ---------- 第 7 步：轮次结束检查 ----------
        # 检查是否需要强制合成、覆盖不足兜底、重新规划等

        if force_synthesis_reason and not requirement_state.output_contract.required:
            synthesized = synthesize_from_evidence(force_synthesis_reason)
            if synthesized is not None:
                return synthesized

        if coverage_insufficient_results >= 4 and requirement_state.output_contract.required:
            fallback_result = try_generate_coverage_limited_pptx(
                f"{coverage_insufficient_results} tool results reported data coverage insufficient"
            )
            if fallback_result is not None:
                return fallback_result

        if run_state.should_request_replan():
            replan_message = run_state.replan_prompt()
            run_state.mark_replan_requested()
            tool_context.runtime_state["agent_run_state"] = run_state.as_dict()
            tool_context.audit_events.append(
                {
                    "event": "agent_replan_requested",
                    "job_id": tool_context.job_id,
                    "trace_id": tool_context.trace_id,
                    "reason": "actions_without_goal_progress_or_repeated_failure",
                    "actions_without_goal_progress": run_state.consecutive_without_goal_progress,
                    "consecutive_failures": run_state.consecutive_failures,
                }
            )
            if _is_anthropic_provider(provider):
                conversation.add_user_message(replan_message)
            else:
                openai_messages.append({"role": "user", "content": replan_message})
            if run_state.should_stop_for_stagnation():
                if requirement_state.output_contract.required:
                    contract_reminder_count += 1
                    reminder = requirement_state.reminder()
                    if _is_anthropic_provider(provider):
                        conversation.add_user_message(reminder)
                    else:
                        openai_messages.append({"role": "user", "content": reminder})
                else:
                    synthesized = synthesize_from_evidence(
                        "two semantic replans did not produce goal or obligation progress"
                    )
                    if synthesized is not None:
                        return synthesized

    # A tool-less synthesis cannot satisfy an open artifact/output obligation.
    # Return an explicit typed incomplete terminal state instead of pretending a
    # prose answer is a usable fallback.
    if requirement_state.output_contract.required and not requirement_state.is_satisfied:
        return finalize_blocked("turn_safety_boundary_with_open_output_contract")

    # For answer-only tasks, return the best evidence-backed response at the
    # emergency boundary rather than a sentinel.
    synthesized = synthesize_from_evidence("agent turn safety boundary reached")
    if synthesized is not None:
        return synthesized

    return AgentLoopResult(
        response_text="[Max tool turns reached]",
        usage=_usage_or_none(total_usage),
        num_turns=turn_count,
        citations=list(citations) or None,
        route=route_policy.route,
        route_policy_version=route_decision.route_policy_version,
        output_contract_status=requirement_state.output_contract_status,
        task_contract_status=requirement_state.status,
        requirements=requirement_state.as_dict()["requirements"],
        termination_reason="max_turns_reached",
        run_state=run_state.as_dict(),
        tool_scheduler_ledger=(
            tool_context.runtime_state.get("tool_scheduler_ledger")
            if isinstance(tool_context.runtime_state.get("tool_scheduler_ledger"), dict)
            else None
        ),
        run_budget=run_budget.as_dict(),
        route_decision=route_decision.as_dict(),
        model_tier=route_decision.model_tier,
        budget_class=route_decision.budget_class,
        model_routing={
            "requested_tier": route_decision.model_tier,
            "model_override": selected_model_override,
            "provider_default_model": getattr(provider, "model", None),
        },
    )
