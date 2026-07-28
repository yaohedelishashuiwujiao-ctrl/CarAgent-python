from __future__ import annotations

from pydantic import BaseModel, Field


class KnowledgeMetric(BaseModel):
    label: str
    value: str
    hint: str | None = None


class KnowledgeStage(BaseModel):
    key: str
    name: str
    status: str
    summary: str
    metrics: list[KnowledgeMetric] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class KnowledgeSampleRow(BaseModel):
    id: str
    brand: str
    model: str
    year: str
    source_type: str
    official_url: str
    selector_hint: str | None = None
    market: str | None = None
    language: str | None = None
    status: str | None = None
    artifact_path: str | None = None
    text_path: str | None = None
    bytes: int | None = None
    final_url: str | None = None
    downloaded_at: str | None = None
    parent_id: str | None = None


class KnowledgeVersion(BaseModel):
    id: str
    name: str
    state: str
    detail: str


class KnowledgeWorkspaceStatus(BaseModel):
    generated_at: str
    snapshot_name: str
    metrics: list[KnowledgeMetric] = Field(default_factory=list)
    stages: list[KnowledgeStage] = Field(default_factory=list)
    source_samples: list[KnowledgeSampleRow] = Field(default_factory=list)
    artifact_samples: list[KnowledgeSampleRow] = Field(default_factory=list)
    versions: list[KnowledgeVersion] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class KnowledgeSearchHit(BaseModel):
    id: str
    brand: str
    model: str
    year: str
    source_type: str
    official_url: str
    artifact_path: str
    text_path: str | None = None
    score: float
    title: str
    excerpt: str
    matched_terms: list[str] = Field(default_factory=list)


class KnowledgeSearchResponse(BaseModel):
    query: str
    top_k: int
    total_matches: int
    hits: list[KnowledgeSearchHit] = Field(default_factory=list)
