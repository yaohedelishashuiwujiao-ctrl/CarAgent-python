from __future__ import annotations

from pydantic import BaseModel, Field


class EvidenceItem(BaseModel):
    id: str
    title: str
    content: str
    evidence_type: str
    source_type: str
    source_ref: str | None = None
    confidence: float | None = None
    review_status: str = "candidate"
    vehicle_instance_id: int | None = None
    system_id: int | None = None
    entity_type_id: int | None = None
    metadata: dict = Field(default_factory=dict)
    created_at: str | None = None


class EvidenceCreate(BaseModel):
    title: str
    content: str
    evidence_type: str = "manual_note"
    source_type: str = "manual"
    source_ref: str | None = None
    confidence: float | None = 1.0
    review_status: str = "reviewed"
    vehicle_instance_id: int | None = None
    system_id: int | None = None
    entity_type_id: int | None = None
    metadata: dict = Field(default_factory=dict)


class EvidenceSummary(BaseModel):
    total_count: int
    reviewed_count: int
    candidate_count: int
    rejected_count: int
    low_confidence_count: int
    source_counts: dict[str, int]
    type_counts: dict[str, int]
