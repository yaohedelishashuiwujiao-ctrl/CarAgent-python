from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from .task_router import TaskRoute, classify_l0


ROUTE_POLICY_VERSION = os.getenv("CLAWD_ROUTE_POLICY_VERSION", "2026-07-18-v1")


@dataclass(frozen=True)
class RouteDecision:
    route: str
    confidence: float
    execution_path: str
    model_tier: str
    tool_profile: str
    budget_class: str
    estimated_cost: int
    max_model_turns: int
    max_tool_calls: int
    max_input_tokens: int
    expected_evidence_kinds: tuple[str, ...] = ()
    preferred_tools: tuple[str, ...] = ()
    fallback_tools: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()
    route_policy_version: str = ROUTE_POLICY_VERSION
    l0_route: TaskRoute | None = None

    @property
    def tool_allowlist(self) -> set[str]:
        return {name.lower() for name in (*self.preferred_tools, *self.fallback_tools)}

    def as_dict(self) -> dict[str, Any]:
        return {
            "route": self.route,
            "confidence": self.confidence,
            "execution_path": self.execution_path,
            "model_tier": self.model_tier,
            "tool_profile": self.tool_profile,
            "budget_class": self.budget_class,
            "estimated_cost": self.estimated_cost,
            "max_model_turns": self.max_model_turns,
            "max_tool_calls": self.max_tool_calls,
            "max_input_tokens": self.max_input_tokens,
            "expected_evidence_kinds": list(self.expected_evidence_kinds),
            "preferred_tools": list(self.preferred_tools),
            "fallback_tools": list(self.fallback_tools),
            "reason_codes": list(self.reason_codes),
            "route_policy_version": self.route_policy_version,
            "l0_route": self.l0_route.as_dict() if self.l0_route is not None else None,
        }


@dataclass(frozen=True)
class RouteCard:
    route: str
    reason_code: str
    preferred_tools: tuple[str, ...]
    fallback_tools: tuple[str, ...]
    tool_profile: str
    budget_class: str
    model_tier: str
    estimated_cost: int
    expected_evidence_kinds: tuple[str, ...]
    signals: tuple[tuple[str, float], ...]
    confidence_base: float = 0.64

    def score(self, text: str) -> float:
        lowered = text.lower()
        return sum(weight for signal, weight in self.signals if signal.lower() in lowered)


ROUTE_CARDS: tuple[RouteCard, ...] = (
    RouteCard(
        route="artifact_generation",
        reason_code="artifact_signal",
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
        tool_profile="artifact_generation",
        budget_class="artifact",
        model_tier="strong",
        estimated_cost=8,
        expected_evidence_kinds=("sql", "structured_data", "knowledge", "web_fetch"),
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
    RouteCard(
        route="vehicle_spec",
        reason_code="vehicle_spec_signal",
        preferred_tools=("SubjectsAttributeLookup", "SubjectsAttributeStats", "SubjectsDataCatalogSearch", "SubjectsSqlQuery"),
        fallback_tools=(
            "SubjectsSqlSchema",
            "SubjectsSqlGlob",
            "KnowledgeSearch",
            "KnowledgeFetch",
            "WebSearch",
            "WebFetch",
            "StructuredOutput",
            "SendUserMessage",
        ),
        tool_profile="vehicle_spec_primary",
        budget_class="lookup",
        model_tier="standard",
        estimated_cost=1,
        expected_evidence_kinds=("sql", "structured_data"),
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
    RouteCard(
        route="manual_qa",
        reason_code="manual_qa_signal",
        preferred_tools=("KnowledgeSearch", "KnowledgeFetch"),
        fallback_tools=("SubjectsSqlSchema", "SubjectsSqlQuery", "WebSearch", "WebFetch", "StructuredOutput", "SendUserMessage"),
        tool_profile="manual_qa_primary",
        budget_class="normal",
        model_tier="standard",
        estimated_cost=2,
        expected_evidence_kinds=("knowledge", "web_fetch"),
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
    RouteCard(
        route="trend_analysis",
        reason_code="trend_analysis_signal",
        preferred_tools=("KnowledgeSearch", "KnowledgeFetch", "SubjectsAttributeLookup", "SubjectsAttributeStats", "SubjectsDataCatalogSearch", "SubjectsSqlQuery"),
        fallback_tools=(
            "SubjectsSqlSchema",
            "SubjectsSqlGlob",
            "WebSearch",
            "WebFetch",
            "AutoChartGenerate",
            "AutoPptxGenerate",
            "StructuredOutput",
            "SendUserMessage",
        ),
        tool_profile="trend_analysis_primary",
        budget_class="analysis",
        model_tier="standard",
        estimated_cost=4,
        expected_evidence_kinds=("sql", "structured_data", "knowledge", "web_fetch"),
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


def decide_route(prompt: str, *, allow_deterministic: bool = True) -> RouteDecision:
    text = " ".join(str(prompt or "").strip().split())
    l0_route = classify_l0(text)
    if allow_deterministic and l0_route.deterministic:
        return RouteDecision(
            route="vehicle_spec",
            confidence=l0_route.confidence,
            execution_path="deterministic_workflow",
            model_tier="cheap",
            tool_profile=f"deterministic_{l0_route.task_type}",
            budget_class="lookup",
            estimated_cost=1,
            max_model_turns=1,
            max_tool_calls=max(1, len(l0_route.attributes) or 1),
            max_input_tokens=0,
            expected_evidence_kinds=("structured_data",),
            preferred_tools=("SubjectsAttributeLookup",),
            reason_codes=(l0_route.reason,),
            l0_route=l0_route,
        )

    if not text:
        return _general_decision("empty_prompt", l0_route=l0_route)

    scored = sorted(
        ((card.score(text), index, card) for index, card in enumerate(ROUTE_CARDS)),
        key=lambda item: (item[0], -item[1]),
        reverse=True,
    )
    artifact = next((item for item in scored if item[2].route == "artifact_generation"), None)
    if artifact is not None and artifact[0] >= 2.0:
        best_score, _, best_card = artifact
    else:
        best_score, _, best_card = scored[0]
    if best_score < 2.0:
        if l0_route.task_type == "no_tool_explanation":
            return RouteDecision(
                route="general",
                confidence=max(0.72, l0_route.confidence),
                execution_path="agent_loop",
                model_tier="cheap",
                tool_profile="no_tool_explanation",
                budget_class="lookup",
                estimated_cost=1,
                max_model_turns=2,
                max_tool_calls=0,
                max_input_tokens=16_000,
                reason_codes=(l0_route.reason,),
                l0_route=l0_route,
            )
        return _general_decision(l0_route.reason or "no_route_signal", l0_route=l0_route)

    confidence = min(0.95, best_card.confidence_base + best_score / 20)
    max_model_turns, max_tool_calls, max_input_tokens = _limits_for_budget(best_card.budget_class)
    return RouteDecision(
        route=best_card.route,
        confidence=confidence,
        execution_path="agent_loop",
        model_tier=best_card.model_tier,
        tool_profile=best_card.tool_profile,
        budget_class=best_card.budget_class,
        estimated_cost=best_card.estimated_cost,
        max_model_turns=max_model_turns,
        max_tool_calls=max_tool_calls,
        max_input_tokens=max_input_tokens,
        expected_evidence_kinds=best_card.expected_evidence_kinds,
        preferred_tools=best_card.preferred_tools,
        fallback_tools=best_card.fallback_tools,
        reason_codes=(best_card.reason_code, l0_route.reason),
        l0_route=l0_route,
    )


def estimate_job_cost(prompt: str) -> int:
    return decide_route(prompt).estimated_cost


def _general_decision(reason_code: str, *, l0_route: TaskRoute | None = None) -> RouteDecision:
    return RouteDecision(
        route="general",
        confidence=0.4,
        execution_path="agent_loop",
        model_tier="cheap",
        tool_profile="general",
        budget_class="normal",
        estimated_cost=2,
        max_model_turns=6,
        max_tool_calls=8,
        max_input_tokens=32_000,
        reason_codes=(reason_code,),
        l0_route=l0_route,
    )


def _limits_for_budget(budget_class: str) -> tuple[int, int, int]:
    if budget_class == "lookup":
        return 4, 6, 32_000
    if budget_class == "analysis":
        return 10, 18, 96_000
    if budget_class == "artifact":
        return 16, 32, 160_000
    return 6, 10, 48_000
