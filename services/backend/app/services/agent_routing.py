from __future__ import annotations

from dataclasses import dataclass
from typing import Any

try:
    from agent_runtime.src.routing_decision import decide_route
except Exception:  # pragma: no cover - import fallback for trimmed deployments.
    decide_route = None  # type: ignore[assignment]


@dataclass(frozen=True)
class AgentJobRouteEstimate:
    route: str
    budget_class: str
    model_tier: str
    estimated_cost: int
    reason_code: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "route": self.route,
            "budget_class": self.budget_class,
            "model_tier": self.model_tier,
            "estimated_cost": self.estimated_cost,
            "reason_code": self.reason_code,
        }


def estimate_agent_job_route(prompt: str) -> AgentJobRouteEstimate:
    if decide_route is not None:
        decision = decide_route(prompt)
        reason_code = next(iter(decision.reason_codes), "runtime_route_decision")
        return AgentJobRouteEstimate(
            route=decision.route,
            budget_class=decision.budget_class,
            model_tier=decision.model_tier,
            estimated_cost=decision.estimated_cost,
            reason_code=reason_code,
        )

    text = str(prompt or "").strip().lower()
    if any(signal in text for signal in ("ppt", "pptx", "幻灯片", "报告", "图表", "生成", "导出", "可视化")):
        return AgentJobRouteEstimate(
            route="artifact_generation",
            budget_class="artifact",
            model_tier="strong",
            estimated_cost=8,
            reason_code="artifact_signal",
        )
    if any(signal in text for signal in ("分析", "调研", "对比", "趋势", "竞品", "市场", "洞察", "策略")):
        return AgentJobRouteEstimate(
            route="trend_analysis",
            budget_class="analysis",
            model_tier="standard",
            estimated_cost=4,
            reason_code="analysis_signal",
        )
    if any(signal in text for signal in ("用户手册", "说明书", "使用限制", "故障灯", "警告灯", "功能说明", "adas", "noa", "acc")):
        return AgentJobRouteEstimate(
            route="manual_qa",
            budget_class="normal",
            model_tier="standard",
            estimated_cost=2,
            reason_code="manual_signal",
        )
    if any(signal in text for signal in ("轴距", "车长", "车宽", "车高", "长宽高", "整备质量", "指导价", "续航", "配置", "参数")):
        return AgentJobRouteEstimate(
            route="vehicle_spec",
            budget_class="lookup",
            model_tier="standard",
            estimated_cost=1,
            reason_code="vehicle_spec_signal",
        )
    if text.startswith(("解释", "说明", "讲讲")):
        return AgentJobRouteEstimate(
            route="general",
            budget_class="lookup",
            model_tier="cheap",
            estimated_cost=1,
            reason_code="no_tool_explanation_signal",
        )
    return AgentJobRouteEstimate(
        route="general",
        budget_class="normal",
        model_tier="cheap",
        estimated_cost=2,
        reason_code="general_default",
    )
