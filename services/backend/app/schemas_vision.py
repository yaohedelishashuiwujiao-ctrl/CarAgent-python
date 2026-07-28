from __future__ import annotations

from pydantic import BaseModel, Field


class VisionAnalyzeRequest(BaseModel):
    file_name: str
    image_data_url: str
    confidence: float = 0.25
    iou: float = 0.7
    image_size: int = 960
    vehicle_instance_id: int | None = None
    note: str | None = None


class VisionDetection(BaseModel):
    id: int
    entity_type_id: int | None = None
    entity_type_code: str
    label: str
    system_id: int | None = None
    system_name: str | None = None
    confidence: float
    bbox: list[int]
    polygon: list[int] | None = None
    source: str
    review_status: str = "needs_review"
    reasoning: str


class VisionTask(BaseModel):
    id: int
    file_name: str
    status: str
    detector_name: str
    object_count: int
    ai_summary: str


class VisionAnalyzeResponse(BaseModel):
    task: VisionTask
    image: dict[str, int]
    detections: list[VisionDetection] = Field(default_factory=list)
    annotated_image: str
    ai_summary: str


class VisionRefineRequest(BaseModel):
    file_name: str
    image_data_url: str
    bbox: list[int]
    iterations: int = 5


class VisionRefineResponse(BaseModel):
    file_name: str
    bbox: list[int]
    polygon: list[int]
    annotated_image: str
    mask_coverage: float
    ai_summary: str
