from __future__ import annotations

from pydantic import BaseModel, Field


class DatasetImage(BaseModel):
    id: int
    file_name: str
    source_type: str
    vehicle_hint: str | None = None
    system_id: int | None = None
    width: int | None = None
    height: int | None = None
    annotation_status: str = "unlabeled"
    split: str = "unassigned"
    object_count: int = 0
    quality_score: float | None = None
    created_at: str
    image_data_url: str | None = None


class DatasetImageCreate(BaseModel):
    file_name: str
    source_type: str = "manual_upload"
    vehicle_hint: str | None = None
    system_id: int | None = None
    width: int | None = None
    height: int | None = None
    image_data_url: str | None = None


class DatasetAnnotation(BaseModel):
    id: int
    image_id: int
    entity_type_id: int
    entity_type_code: str
    entity_type_name: str
    bbox: list[float]
    annotation_type: str = "bbox"
    status: str = "draft"
    created_at: str


class DatasetAnnotationCreate(BaseModel):
    image_id: int
    entity_type_id: int
    bbox: list[float]
    annotation_type: str = "bbox"


class DatasetClassStat(BaseModel):
    entity_type_id: int
    entity_type_code: str
    entity_type_name: str
    system_name: str | None = None
    labeled_instances: int
    target_instances: int


class DatasetSummary(BaseModel):
    image_count: int
    unlabeled_count: int
    labeling_count: int
    reviewed_count: int
    train_count: int
    val_count: int
    test_count: int
    class_stats: list[DatasetClassStat] = Field(default_factory=list)


class YoloExportPlan(BaseModel):
    export_name: str
    format: str = "yolo-seg"
    class_count: int
    image_count: int
    train_count: int
    val_count: int
    test_count: int
    classes: list[str]
    notes: list[str]
