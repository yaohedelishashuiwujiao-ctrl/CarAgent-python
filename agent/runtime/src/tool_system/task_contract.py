from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from .context import ToolContext
from .protocol import ToolResult


class RequirementStatus(str, Enum):
    OPEN = "open"
    SATISFIED = "satisfied"
    BLOCKED = "blocked"
    NEEDS_USER = "needs_user"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True)
class ArtifactRequirement:
    artifact_type: str
    exact_count: int | None = None
    require_table_per_slide: bool = False
    require_source_per_slide: bool = False


@dataclass(frozen=True)
class OutputContract:
    required_artifacts: tuple[ArtifactRequirement, ...] = ()
    structured_json_required: bool = False

    @property
    def required(self) -> bool:
        return bool(self.required_artifacts or self.structured_json_required)

    @classmethod
    def from_user_request(cls, text: str) -> "OutputContract":
        lowered = (text or "").lower()
        artifacts: list[ArtifactRequirement] = []

        ppt_requested = any(term in lowered for term in ("ppt", "pptx", "幻灯片", "演示文稿"))
        if ppt_requested:
            count = _extract_ppt_slide_count(text)
            per_slide_scope = any(term in lowered for term in ("每页", "逐页", "each slide", "per slide"))
            require_table = per_slide_scope and any(term in lowered for term in ("对比表", "比较表", "table"))
            require_sources = (
                (per_slide_scope and any(term in lowered for term in ("来源", "source", "引用")))
                or ("页脚" in lowered and any(term in lowered for term in ("来源", "source", "引用")))
            )
            artifacts.append(
                ArtifactRequirement(
                    "pptx",
                    exact_count=count,
                    require_table_per_slide=require_table,
                    require_source_per_slide=require_sources,
                )
            )

        chart_requested = any(term in lowered for term in ("图表", "画图", "生成图", "可视化"))
        if chart_requested and not ppt_requested:
            artifacts.append(ArtifactRequirement("chart"))

        structured = bool(re.search(r"(?:json|结构化\s*(?:json|输出))", lowered))
        return cls(required_artifacts=tuple(artifacts), structured_json_required=structured)


@dataclass
class Requirement:
    id: str
    description: str
    status: RequirementStatus = RequirementStatus.OPEN
    artifact_paths: list[str] = field(default_factory=list)
    evidence_kinds: tuple[str, ...] = ()
    evidence_ids: list[int] = field(default_factory=list)
    minimum_evidence_count: int = 0
    blocking_reason: str | None = None


@dataclass
class TaskRequirementState:
    output_contract: OutputContract
    requirements: dict[str, Requirement]

    @classmethod
    def from_user_request(cls, text: str) -> "TaskRequirementState":
        contract = OutputContract.from_user_request(text)
        requirements: dict[str, Requirement] = {}
        for artifact in contract.required_artifacts:
            suffix = f" with exactly {artifact.exact_count} slides" if artifact.exact_count is not None else ""
            constraints: list[str] = []
            if artifact.require_table_per_slide:
                constraints.append("a comparison/data table on every slide")
            if artifact.require_source_per_slide:
                constraints.append("a visible source footer on every slide")
            if constraints:
                suffix += ", including " + " and ".join(constraints)
            requirements[f"artifact:{artifact.artifact_type}"] = Requirement(
                id=f"artifact:{artifact.artifact_type}",
                description=f"Generate a valid {artifact.artifact_type} artifact{suffix}.",
            )
        if contract.structured_json_required:
            requirements["output:structured_json"] = Requirement(
                id="output:structured_json",
                description="Return the final response through StructuredOutput.",
            )
        return cls(contract, requirements)

    def require_evidence(
        self,
        requirement_id: str,
        description: str,
        *,
        evidence_kinds: tuple[str, ...],
        minimum_count: int = 1,
    ) -> None:
        self.requirements.setdefault(
            requirement_id,
            Requirement(
                id=requirement_id,
                description=description,
                evidence_kinds=evidence_kinds,
                minimum_evidence_count=max(1, minimum_count),
            ),
        )

    def update_plan_completion(self, *, has_plan: bool, plan_complete: bool) -> None:
        """Make a model-authored plan a real completion obligation once used."""
        if not has_plan:
            return
        requirement = self.requirements.setdefault(
            "plan:completion",
            Requirement(
                id="plan:completion",
                description="Complete or explicitly revise every step in the model-authored execution plan before finishing.",
            ),
        )
        requirement.status = RequirementStatus.SATISFIED if plan_complete else RequirementStatus.OPEN
        requirement.blocking_reason = None if plan_complete else "The model-authored plan still has pending or in-progress steps."

    @property
    def is_satisfied(self) -> bool:
        return all(item.status in {RequirementStatus.SATISFIED, RequirementStatus.NOT_APPLICABLE} for item in self.requirements.values())

    @property
    def status(self) -> str:
        if not self.requirements:
            return "not_required"
        return "satisfied" if self.is_satisfied else "unmet"

    @property
    def output_contract_status(self) -> str:
        output_requirements = [
            item
            for key, item in self.requirements.items()
            if key.startswith("artifact:") or key.startswith("output:")
        ]
        if not output_requirements:
            return "not_required"
        return (
            "satisfied"
            if all(item.status in {RequirementStatus.SATISFIED, RequirementStatus.NOT_APPLICABLE} for item in output_requirements)
            else "unmet"
        )

    def update_from_evidence(self, citations: list[dict[str, Any]]) -> None:
        for requirement in self.requirements.values():
            if not requirement.evidence_kinds:
                continue
            for citation in citations:
                kind = str(citation.get("source_type") or "").lower()
                citation_id = citation.get("citation_id")
                if kind not in requirement.evidence_kinds or not isinstance(citation_id, int):
                    continue
                if citation_id not in requirement.evidence_ids:
                    requirement.evidence_ids.append(citation_id)
            if len(requirement.evidence_ids) >= requirement.minimum_evidence_count:
                requirement.status = RequirementStatus.SATISFIED
                requirement.blocking_reason = None

    def update_from_tool_result(
        self,
        tool_name: str,
        result: ToolResult,
        context: ToolContext,
        *,
        capability_namespace: str = "",
        output_modes: tuple[str, ...] = (),
    ) -> None:
        if result.is_error or not isinstance(result.output, dict):
            return
        lowered = tool_name.lower()
        namespace_tail = (capability_namespace or "").lower().rsplit(".", 1)[-1]
        normalized_modes = {str(item).lower() for item in output_modes}
        produces_pptx = lowered == "autopptxgenerate" or "pptx" in normalized_modes or namespace_tail == "pptx"
        produces_chart = lowered == "autochartgenerate" or namespace_tail == "chart"
        produces_json = lowered == "structuredoutput" or "json" in normalized_modes
        if produces_pptx:
            req = self.requirements.get("artifact:pptx")
            if req is not None:
                valid, reason, path = _validate_pptx_result(result.output, self.output_contract, context)
                if valid:
                    req.status = RequirementStatus.SATISFIED
                    if path:
                        req.artifact_paths.append(path)
                    req.blocking_reason = None
                else:
                    req.blocking_reason = reason
        elif produces_chart:
            req = self.requirements.get("artifact:chart")
            if req is not None:
                path_value = str(result.output.get("file_path") or "").strip()
                try:
                    path = context.ensure_allowed_path(path_value) if path_value else None
                except Exception:
                    path = None
                if path is not None and path.is_file() and path.stat().st_size > 0:
                    req.status = RequirementStatus.SATISFIED
                    req.artifact_paths.append(str(path))
                    req.blocking_reason = None
                else:
                    req.blocking_reason = "Chart artifact is missing or empty."
        elif produces_json:
            req = self.requirements.get("output:structured_json")
            if req is not None and "structured_output" in result.output:
                req.status = RequirementStatus.SATISFIED
                req.blocking_reason = None

    def unmet_requirements(self) -> list[Requirement]:
        return [item for item in self.requirements.values() if item.status == RequirementStatus.OPEN]

    def reminder(self) -> str:
        lines = ["Runtime task contract is not satisfied. Do not finish yet."]
        lines.extend(f"- {item.description}" for item in self.unmet_requirements())
        lines.append("Use an eligible tool to produce and validate the required output. If no eligible path exists, state the concrete boundary.")
        return "\n".join(lines)

    def prompt(self) -> str:
        if not self.requirements:
            return "Task contract: no explicit artifact, structured-output, or evidence requirement was detected."
        return "Task contract (Runtime-enforced):\n" + "\n".join(
            f"- {item.description}" for item in self.requirements.values()
        ) + "\nA text-only unsupported response does not satisfy these requirements."

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "output_contract_status": self.output_contract_status,
            "requirements": [
                {
                    "id": item.id,
                    "description": item.description,
                    "status": item.status.value,
                    "artifact_paths": list(item.artifact_paths),
                    "evidence_kinds": list(item.evidence_kinds),
                    "evidence_ids": list(item.evidence_ids),
                    "minimum_evidence_count": item.minimum_evidence_count,
                    "blocking_reason": item.blocking_reason,
                }
                for item in self.requirements.values()
            ],
        }


def _extract_ppt_slide_count(text: str) -> int | None:
    patterns = (
        r"(?P<count>\d{1,2})\s*页\s*(?:的\s*)?(?:pptx?|幻灯片|演示文稿)",
        r"(?:pptx?|幻灯片|演示文稿).{0,16}?(?P<count>\d{1,2})\s*页",
    )
    for pattern in patterns:
        match = re.search(pattern, text or "", flags=re.IGNORECASE)
        if match:
            count = int(match.group("count"))
            return count if 1 <= count <= 50 else None
    return None


def _validate_pptx_result(
    output: dict[str, Any],
    contract: OutputContract,
    context: ToolContext,
) -> tuple[bool, str | None, str | None]:
    path_value = str(output.get("file_path") or "").strip()
    if not path_value:
        return False, "PPTX result did not include file_path.", None
    try:
        path = context.ensure_allowed_path(path_value)
    except Exception as exc:
        return False, f"PPTX path is outside the allowed workspace: {exc}", None
    if not path.is_file() or path.stat().st_size <= 0:
        return False, "PPTX artifact is missing or empty.", str(path)
    try:
        with zipfile.ZipFile(path) as archive:
            slide_count = sum(
                1
                for name in archive.namelist()
                if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)
            )
    except (OSError, zipfile.BadZipFile) as exc:
        return False, f"PPTX artifact cannot be opened: {exc}", str(path)
    requirement = next((item for item in contract.required_artifacts if item.artifact_type == "pptx"), None)
    if requirement and requirement.exact_count is not None and slide_count != requirement.exact_count:
        return False, f"PPTX contains {slide_count} slides; expected {requirement.exact_count}.", str(path)
    if slide_count < 1:
        return False, "PPTX contains no slides.", str(path)
    if requirement and (requirement.require_table_per_slide or requirement.require_source_per_slide):
        manifest = output.get("slide_manifest")
        if not isinstance(manifest, list) or len(manifest) != slide_count:
            return False, "PPTX result did not include a verifiable per-slide content manifest.", str(path)
        if any(not isinstance(item, dict) for item in manifest):
            return False, "PPTX per-slide content manifest is invalid.", str(path)
        if requirement.require_table_per_slide and any(not bool(item.get("has_table")) for item in manifest):
            return False, "PPTX is missing a required table on one or more slides.", str(path)
        if requirement.require_source_per_slide and any(not bool(item.get("has_source_footer")) for item in manifest):
            return False, "PPTX is missing a required visible source footer on one or more slides.", str(path)
    return True, None, str(path)
