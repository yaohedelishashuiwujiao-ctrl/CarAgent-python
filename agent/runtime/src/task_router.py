from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .tool_system.protocol import ToolResult


ATTRIBUTE_ALIASES: dict[str, tuple[str, ...]] = {
    "轴距": ("轴距", "wheelbase"),
    "整备质量": ("整备质量", "整车质量", "curb weight", "curb_weight"),
    "车长": ("车长", "长度"),
    "车宽": ("车宽", "宽度"),
    "车高": ("车高", "高度"),
    "前轮距": ("前轮距", "前轮距宽", "前后轮距", "轮距"),
    "后轮距": ("后轮距", "后轮距宽", "前后轮距", "轮距"),
    "前悬架类型": ("前悬架", "前悬架类型", "前悬"),
    "后悬架类型": ("后悬架", "后悬架类型", "后悬"),
    "空气悬架": ("空气悬架",),
    "可变悬架功能": ("可变悬架", "悬架软硬调节", "悬架高低调节"),
    "续航": ("续航", "CLTC", "WLTC"),
    "电池容量": ("电池容量", "电池电量"),
    "指导价": ("指导价", "售价", "价格"),
}

QUERY_PREFIX_RE = re.compile(r"^(请|帮我|麻烦|查询|查一下|查|看看|获取|平台数据中|数据库中|受控数据库中)+")
FIELD_CATALOG_SIGNALS = ("字段", "字段目录", "定义", "覆盖", "口径", "有哪些字段")
COMPLEX_SIGNALS = ("ppt", "报告", "分析", "对比", "趋势", "生成", "制作")
STATS_SIGNALS = ("统计", "均值", "平均", "极值", "最大", "最小", "分布", "样本", "覆盖")
COHORT_VALUE_RE = re.compile(r"(?<![A-Za-z0-9])[A-Z][A-Z0-9+_-]{1,}(?![A-Za-z0-9])")
TASK_FILLER_TERMS = (
    "帮我",
    "请",
    "麻烦",
    "调研",
    "查询",
    "查一下",
    "查",
    "看看",
    "获取",
    "全部",
    "所有",
    "具备",
    "配备",
    "是否",
    "有没有",
    "有无",
    "哪些",
    "情况",
    "统计",
    "车型",
    "车辆",
)


@dataclass(frozen=True)
class TaskRoute:
    task_type: str
    confidence: float
    entities: tuple[str, ...] = ()
    attributes: tuple[str, ...] = ()
    recommended_path: str = "agent_loop"
    reason: str = ""

    @property
    def deterministic(self) -> bool:
        return self.recommended_path == "deterministic_workflow"

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_type": self.task_type,
            "confidence": self.confidence,
            "entities": list(self.entities),
            "attributes": list(self.attributes),
            "recommended_path": self.recommended_path,
            "reason": self.reason,
        }


def classify_l0(prompt: str) -> TaskRoute:
    """Zero-model-cost router for high-confidence, high-frequency tasks."""
    text = " ".join(str(prompt or "").strip().split())
    if not text:
        return _agent_loop("empty_prompt")

    attributes = _extract_attributes(text)
    cohort_value = _extract_cohort_value(text)
    if attributes and _looks_like_attribute_stats_query(text):
        return TaskRoute(
            task_type="vehicle_attribute_stats",
            confidence=0.82,
            attributes=tuple(attributes),
            recommended_path="deterministic_workflow",
            reason="known vehicle attributes + statistics request matched governed aggregate path",
        )

    if any(signal.lower() in text.lower() for signal in COMPLEX_SIGNALS):
        return _agent_loop("complex_task_signal")

    if not attributes and not cohort_value:
        if _looks_like_no_tool_explanation(text):
            return TaskRoute(
                task_type="no_tool_explanation",
                confidence=0.82,
                recommended_path="agent_loop",
                reason="explanation task; no deterministic factual data path selected",
            )
        return _agent_loop("no_known_attribute")

    if _looks_like_field_catalog_query(text):
        return TaskRoute(
            task_type="field_catalog_query",
            confidence=0.9,
            attributes=tuple(attributes),
            recommended_path="deterministic_workflow",
            reason="field/catalog definition query matched known attribute aliases",
        )

    entity = _extract_entity(text, attributes[0]) if attributes else ""
    if entity:
        return TaskRoute(
            task_type="single_vehicle_attribute_query",
            confidence=0.88,
            entities=(entity,),
            attributes=(attributes[0],),
            recommended_path="deterministic_workflow",
            reason="single entity + known vehicle attribute matched L0 route",
        )

    if cohort_value and _looks_like_cohort_attribute_query(text):
        attribute_query = attributes[0] if attributes else _derive_attribute_query(text, cohort_value)
        return TaskRoute(
            task_type="cohort_attribute_query",
            confidence=0.74 if attributes else 0.66,
            entities=(cohort_value,),
            attributes=(attribute_query or text,),
            recommended_path="deterministic_workflow",
            reason="cohort value + vehicle attribute query shape matched L0 route; filter field will be resolved from data",
        )

    return _agent_loop("entity_not_extracted")


def build_subjects_lookup_answer(route: TaskRoute, results: list[ToolResult]) -> dict[str, Any]:
    citations: list[dict[str, Any]] = []
    sections: list[str] = []
    satisfied = True
    for index, result in enumerate(results, start=1):
        output = result.output if isinstance(result.output, dict) else {}
        rows = output.get("rows") if isinstance(output.get("rows"), list) else []
        attr = str(output.get("attribute_keyword") or (route.attributes[index - 1] if index - 1 < len(route.attributes) else ""))
        entity = str(output.get("entity_keyword") or (route.entities[0] if route.entities else "*"))
        citation_id = len(citations) + 1
        citation_content = _compact_rows(rows) if rows else _compact_no_row_evidence(output)
        citations.append(
            {
                "citation_id": citation_id,
                "source_type": "structured_data",
                "source": result.name,
                "title": "SubjectsAttributeLookup deterministic workflow result",
                "content": citation_content,
                "metadata": {
                    "task_router": route.as_dict(),
                    "entity_keyword": entity,
                    "attribute_keyword": attr,
                    "row_count": len(rows),
                    "filtered_entity_count": output.get("filtered_entity_count"),
                    "outcome_status": getattr(result.outcome_status, "value", str(result.outcome_status)),
                    "reason_code": result.reason_code,
                },
            }
        )
        if not rows:
            satisfied = False
            sections.append(
                f"### {attr}\n"
                f"未在当前受控 Subjects 数据集中查到目标属性的有效匹配记录 [{citation_id}]。\n"
                f"{_format_no_row_boundary(output)}\n\n"
                f"边界：{output.get('coverage_boundary') or '当前结果只代表平台已治理数据覆盖范围。'}"
            )
            continue
        if route.task_type == "single_vehicle_attribute_query":
            sections.append(_format_single_attribute_section(entity, attr, rows, citation_id))
        else:
            sections.append(_format_catalog_section(attr, rows, citation_id))

    title = "## 快速路径查询结果"
    if route.task_type == "single_vehicle_attribute_query" and route.entities and route.attributes:
        title = f"## {route.entities[0]} {route.attributes[0]}查询结果"
    text = title + "\n\n" + "\n\n".join(sections)
    text += "\n\n### 执行路径\nL0 TaskRouter 命中固定 workflow，未调用模型进行工具规划。"
    return {
        "text": text,
        "citations": citations,
        "task_contract_status": "satisfied" if satisfied else "unmet",
        "termination_reason": "deterministic_workflow_completed" if satisfied else "deterministic_workflow_no_data",
    }


def build_subjects_stats_answer(route: TaskRoute, results: list[ToolResult]) -> dict[str, Any]:
    result = results[0] if results else None
    output = result.output if result is not None and isinstance(result.output, dict) else {}
    groups = output.get("results") if isinstance(output.get("results"), list) else []
    citation_id = 1
    citations = [
        {
            "citation_id": citation_id,
            "source_type": "structured_data",
            "source": result.name if result is not None else "SubjectsAttributeStats",
            "title": "SubjectsAttributeStats deterministic workflow result",
            "content": str(groups)[:6000],
            "metadata": {
                "task_router": route.as_dict(),
                "attribute_keywords": output.get("attribute_keywords"),
                "entity_keyword": output.get("entity_keyword"),
                "filter_value_keyword": output.get("filter_value_keyword"),
                "populated_numeric_value_count": output.get("populated_numeric_value_count"),
                "outcome_status": getattr(result.outcome_status, "value", str(result.outcome_status)) if result is not None else "unknown",
                "reason_code": result.reason_code if result is not None else None,
            },
        }
    ]
    lines = ["## 车辆属性统计结果", "", "| 属性 | 样本数 | 均值 | 最小值 | 最大值 | 标准差 |", "|---|---:|---:|---:|---:|---:|"]
    satisfied = False
    for group in groups:
        if not isinstance(group, dict):
            continue
        for row in group.get("stats") or []:
            if not isinstance(row, dict):
                continue
            count = int(row.get("numeric_value_count") or 0)
            if count <= 0:
                continue
            satisfied = True
            unit = row.get("attribute_unit") or ""
            lines.append(
                "| {name} | {count} | {avg} | {minv} | {maxv} | {std} |".format(
                    name=row.get("attribute_name") or group.get("attribute_keyword") or "",
                    count=count,
                    avg=_format_number(row.get("avg_value"), unit),
                    minv=_format_number(row.get("min_value"), unit),
                    maxv=_format_number(row.get("max_value"), unit),
                    std=_format_number(row.get("stddev_pop"), unit),
                )
            )
    if not satisfied:
        lines.extend(["", "当前受控数据集中未查到可聚合的数值型样本。"])
    else:
        lines.extend(
            [
                "",
                f"证据来源：SubjectsAttributeStats 聚合当前权限范围内 active 车型记录 [{citation_id}]。",
                "数据边界：统计只覆盖当前受控 Subjects 数据集中的数值型车型属性，不代表外部全市场完整数据。",
            ]
        )
    return {
        "text": "\n".join(lines),
        "citations": citations,
        "task_contract_status": "satisfied" if satisfied else "unmet",
        "termination_reason": "deterministic_workflow_completed" if satisfied else "deterministic_workflow_no_data",
    }


def _agent_loop(reason: str) -> TaskRoute:
    return TaskRoute(task_type="unknown", confidence=0.0, recommended_path="agent_loop", reason=reason)


def _format_number(value: Any, unit: Any = "") -> str:
    if value is None or value == "":
        return "-"
    try:
        number = float(value)
        text = f"{number:.2f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        text = str(value)
    return f"{text} {unit}".strip()


def _compact_no_row_evidence(output: dict[str, Any]) -> str:
    payload = {
        "attribute_candidates": (output.get("attribute_candidates") or [])[:3] if isinstance(output.get("attribute_candidates"), list) else [],
        "filter_value_keyword": output.get("filter_value_keyword"),
        "filter_attribute_candidates": (output.get("filter_attribute_candidates") or [])[:3] if isinstance(output.get("filter_attribute_candidates"), list) else [],
        "filtered_entity_count": output.get("filtered_entity_count"),
        "coverage_boundary": output.get("coverage_boundary"),
    }
    return str(payload)


def _format_no_row_boundary(output: dict[str, Any]) -> str:
    lines: list[str] = []
    attr_candidates = output.get("attribute_candidates") if isinstance(output.get("attribute_candidates"), list) else []
    if attr_candidates:
        names = [
            f"{item.get('attribute_name')}({item.get('attribute_id')})"
            for item in attr_candidates[:3]
            if isinstance(item, dict)
        ]
        lines.append(f"- 目标字段候选：{', '.join(names)}")
    filter_value = output.get("filter_value_keyword")
    filter_candidates = output.get("filter_attribute_candidates") if isinstance(output.get("filter_attribute_candidates"), list) else []
    if filter_value and filter_candidates:
        names = [
            f"{item.get('attribute_name')}({item.get('attribute_id')}, 匹配{item.get('matched_vehicle_count')}条)"
            for item in filter_candidates[:3]
            if isinstance(item, dict)
        ]
        lines.append(f"- 过滤值 `{filter_value}` 由数据分布自动匹配到：{', '.join(names)}")
    if output.get("filtered_entity_count") is not None:
        lines.append(f"- 过滤后车型/配置数量：{output.get('filtered_entity_count')}")
    return "\n".join(lines)


def _extract_attributes(text: str) -> list[str]:
    lowered = text.lower()
    found: list[str] = []
    for canonical, aliases in ATTRIBUTE_ALIASES.items():
        if any(alias.lower() in lowered for alias in aliases):
            found.append(canonical)
    return found


def _looks_like_field_catalog_query(text: str) -> bool:
    lowered = text.lower()
    return any(signal.lower() in lowered for signal in FIELD_CATALOG_SIGNALS)


def _looks_like_no_tool_explanation(text: str) -> bool:
    return any(text.startswith(prefix) for prefix in ("解释", "说明", "讲讲")) and not any(
        signal in text for signal in ("查询", "查", "数据库", "平台数据", "具体车型")
    )


def _extract_cohort_value(text: str) -> str:
    matches = COHORT_VALUE_RE.findall(text or "")
    return matches[0] if matches else ""


def _looks_like_cohort_attribute_query(text: str) -> bool:
    return any(signal in text for signal in ("全部", "所有", "哪些", "情况", "统计", "调研"))


def _looks_like_attribute_stats_query(text: str) -> bool:
    return any(signal in text for signal in STATS_SIGNALS) and any(signal in text for signal in ("全部", "所有", "车型", "车辆", "样本"))


def _derive_attribute_query(text: str, cohort_value: str) -> str:
    query = str(text or "")
    if cohort_value:
        query = query.replace(cohort_value, "")
    for term in TASK_FILLER_TERMS:
        query = query.replace(term, "")
    query = re.sub(r"[，,。；;：:？?！!\s]+", "", query)
    query = query.strip("的了中内外")
    return query


def _extract_entity(text: str, attr: str) -> str:
    attr_pos = text.find(attr)
    if attr_pos <= 0:
        return ""
    prefix = text[:attr_pos]
    prefix = prefix.rstrip("的 ：:，,")
    prefix = re.sub(r".*[，,。；;]\s*", "", prefix)
    prefix = QUERY_PREFIX_RE.sub("", prefix).strip()
    prefix = prefix.removeprefix("平台数据中").removeprefix("数据库中").strip()
    prefix = prefix.rstrip("的")
    if not prefix or len(prefix) > 40:
        return ""
    if any(word in prefix for word in ("字段", "定义", "单位", "全部", "所有")):
        return ""
    return prefix


def _format_single_attribute_section(entity: str, attr: str, rows: list[Any], citation_id: int) -> str:
    row = rows[0] if isinstance(rows[0], dict) else {}
    vehicle = row.get("vehicle_name") or entity
    attribute_name = row.get("attribute_name") or attr
    unit = row.get("unit") or row.get("attribute_unit") or ""
    value = row.get("value_number")
    if value is None or value == "":
        value = row.get("value_text")
    value_text = f"{value} {unit}".strip() if value is not None else "当前记录无明确值"
    return (
        f"### {vehicle}\n"
        f"- 查询属性：{attribute_name}\n"
        f"- 结果：{value_text} [{citation_id}]\n"
        f"- 字段：`{row.get('attribute_code') or 'unknown'}`，单位：{unit or '未声明'}\n"
        f"- 数据边界：结果来自当前受控 Subjects 数据集；如果同名车型存在多版本，需要进一步限定年款/配置。"
    )


def _format_catalog_section(attr: str, rows: list[Any], citation_id: int) -> str:
    unique: dict[tuple[Any, Any], dict[str, Any]] = {}
    vehicles: set[str] = set()
    units: set[str] = set()
    for item in rows:
        if not isinstance(item, dict):
            continue
        key = (item.get("attribute_id"), item.get("attribute_code"))
        unique.setdefault(key, item)
        if item.get("vehicle_name"):
            vehicles.add(str(item["vehicle_name"]))
        unit = item.get("unit") or item.get("attribute_unit")
        if unit:
            units.add(str(unit))
    lines = [f"### {attr}", f"- 匹配字段数：{len(unique)}，样本记录数：{len(rows)} [{citation_id}]"]
    if units:
        lines.append(f"- 单位：{', '.join(sorted(units))}")
    for item in list(unique.values())[:6]:
        lines.append(
            f"- `{item.get('attribute_code')}`：{item.get('attribute_name')}，单位 {item.get('unit') or item.get('attribute_unit') or '未声明'}"
        )
    lines.append(f"- 覆盖样本：{len(vehicles)} 个车型/配置；完整覆盖以当前工具返回上限和数据权限为边界。")
    return "\n".join(lines)


def _compact_rows(rows: list[Any]) -> str:
    safe_rows = rows[:20]
    return str(safe_rows)[:4000]
