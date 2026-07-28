from __future__ import annotations

from datetime import datetime
from functools import lru_cache

from backend.app.config import settings
from backend.app.db import DatabaseUnavailable
from backend.app.repositories.dataset import DatasetRepository, MemoryDatasetRepository, MySqlDatasetRepository
from backend.app.schemas_dataset import (
    DatasetAnnotation,
    DatasetAnnotationCreate,
    DatasetClassStat,
    DatasetImage,
    DatasetImageCreate,
    DatasetSummary,
    YoloExportPlan,
)
from backend.app.services.catalog import store


class InMemoryDatasetState:
    def __init__(self) -> None:
        self.images: list[DatasetImage] = [
            DatasetImage(
                id=1,
                file_name="upper_control_arm_0001_a5002dcff9.jpg",
                source_type="manual_upload",
                vehicle_hint="小鹏 X9",
                system_id=1,
                width=1600,
                height=1000,
                annotation_status="reviewed",
                split="train",
                object_count=3,
                quality_score=0.86,
                created_at="2026-06-24T10:00:00",
            ),
            DatasetImage(
                id=2,
                file_name="upper_control_arm_0002_fab1a05e5b.jpg",
                source_type="web_research",
                vehicle_hint=None,
                system_id=2,
                width=1280,
                height=960,
                annotation_status="labeling",
                split="val",
                object_count=1,
                quality_score=0.74,
                created_at="2026-06-24T10:20:00",
            ),
            DatasetImage(
                id=3,
                file_name="upper_control_arm_0003_48e7226db3.jpg",
                source_type="video_frame",
                vehicle_hint=None,
                system_id=4,
                width=1920,
                height=1080,
                annotation_status="unlabeled",
                split="unassigned",
                object_count=0,
                quality_score=0.68,
                created_at="2026-06-24T10:40:00",
            ),
        ]
        self.annotations: list[DatasetAnnotation] = [
            DatasetAnnotation(
                id=1,
                image_id=1,
                entity_type_id=2,
                entity_type_code="upper_control_arm",
                entity_type_name="上控制臂/上摆臂",
                bbox=[250, 420, 640, 620],
                status="reviewed",
                created_at="2026-06-24T10:05:00",
            )
        ]

    def create_image(self, payload: DatasetImageCreate) -> DatasetImage:
        image = DatasetImage(
            id=self._next_id(self.images),
            file_name=payload.file_name,
            source_type=payload.source_type,
            vehicle_hint=payload.vehicle_hint,
            system_id=payload.system_id,
            width=payload.width,
            height=payload.height,
            annotation_status="unlabeled",
            split="unassigned",
            object_count=0,
            quality_score=None,
            created_at=datetime.now().replace(microsecond=0).isoformat(),
            image_data_url=payload.image_data_url,
        )
        self.images.insert(0, image)
        return image

    def create_annotation(self, payload: DatasetAnnotationCreate) -> DatasetAnnotation:
        image = self._require_image(payload.image_id)
        entity = next((item for item in store.entity_types if item.id == payload.entity_type_id), None)
        if entity is None or entity.category != "component":
            raise ValueError("annotation must use a component entity type")
        self._validate_geometry(payload.annotation_type, payload.bbox)
        annotation = DatasetAnnotation(
            id=self._next_id(self.annotations),
            image_id=image.id,
            entity_type_id=entity.id,
            entity_type_code=entity.code,
            entity_type_name=entity.name,
            bbox=payload.bbox,
            annotation_type=payload.annotation_type,
            status="draft",
            created_at=datetime.now().replace(microsecond=0).isoformat(),
        )
        self.annotations.append(annotation)
        self._refresh_image_annotation_state(image.id)
        return annotation

    def _validate_geometry(self, annotation_type: str, geometry: list[float]) -> None:
        if annotation_type == "bbox":
            if len(geometry) != 4:
                raise ValueError("bbox must contain [x1, y1, x2, y2]")
            return
        if annotation_type == "polygon":
            if len(geometry) < 6 or len(geometry) % 2 != 0:
                raise ValueError("polygon must contain at least 3 points as [x1, y1, x2, y2, x3, y3, ...]")
            return
        raise ValueError(f"unsupported annotation_type: {annotation_type}")

    def list_annotations(self, image_id: int | None = None) -> list[DatasetAnnotation]:
        if image_id is None:
            return self.annotations
        return [item for item in self.annotations if item.image_id == image_id]

    def summary(self) -> DatasetSummary:
        return DatasetSummary(
            image_count=len(self.images),
            unlabeled_count=sum(1 for item in self.images if item.annotation_status == "unlabeled"),
            labeling_count=sum(1 for item in self.images if item.annotation_status == "labeling"),
            reviewed_count=sum(1 for item in self.images if item.annotation_status == "reviewed"),
            train_count=sum(1 for item in self.images if item.split == "train"),
            val_count=sum(1 for item in self.images if item.split == "val"),
            test_count=sum(1 for item in self.images if item.split == "test"),
            class_stats=self._class_stats(),
        )

    def export_plan(self) -> YoloExportPlan:
        component_types = [item for item in store.entity_types if item.category == "component"]
        exportable_images = [item for item in self.images if any(annotation.image_id == item.id for annotation in self.annotations)]
        return YoloExportPlan(
            export_name="chassis_parts_yolo_v0",
            format="yolo-seg",
            class_count=len(component_types),
            image_count=len(exportable_images),
            train_count=sum(1 for item in exportable_images if item.split == "train"),
            val_count=sum(1 for item in exportable_images if item.split == "val"),
            test_count=sum(1 for item in exportable_images if item.split == "test"),
            classes=[item.code for item in component_types],
            notes=[
                "当前导出会包含所有已有标注的图片，适合先跑通分割训练闭环。",
                "类别顺序来自 entity_type.id，必须随模型版本固化。",
                "点击下载会生成 YOLO-seg zip，包含 images/、labels/、data.yaml、manifest.json。",
            ],
        )

    def _class_stats(self) -> list[DatasetClassStat]:
        stats: list[DatasetClassStat] = []
        for entity in store.entity_types:
            if entity.category != "component":
                continue
            system = next((item for item in store.systems if item.id == entity.default_system_id), None)
            seed_count = sum(1 for item in self.annotations if item.entity_type_id == entity.id and item.status in {"draft", "reviewed"})
            stats.append(
                DatasetClassStat(
                    entity_type_id=entity.id,
                    entity_type_code=entity.code,
                    entity_type_name=entity.name,
                    system_name=system.name if system else None,
                    labeled_instances=seed_count,
                    target_instances=300,
                )
            )
        return stats

    def _require_image(self, image_id: int) -> DatasetImage:
        for item in self.images:
            if item.id == image_id:
                return item
        raise ValueError(f"dataset image does not exist: {image_id}")

    def _refresh_image_annotation_state(self, image_id: int) -> None:
        count = sum(1 for item in self.annotations if item.image_id == image_id)
        refreshed = []
        for image in self.images:
            if image.id == image_id:
                data = image.model_dump()
                data["object_count"] = count
                if image.annotation_status == "unlabeled" and count > 0:
                    data["annotation_status"] = "labeling"
                refreshed.append(DatasetImage(**data))
            else:
                refreshed.append(image)
        self.images = refreshed

    @staticmethod
    def _next_id(items: list) -> int:
        return max((item.id for item in items), default=0) + 1


@lru_cache(maxsize=1)
def get_dataset_repository() -> DatasetRepository:
    backend = settings.data_backend.lower()
    if backend == "memory":
        return MemoryDatasetRepository()
    if backend == "mysql":
        if not settings.database_url:
            raise DatabaseUnavailable("DATA_BACKEND=mysql requires DATABASE_URL")
        return MySqlDatasetRepository()
    raise DatabaseUnavailable(f"unsupported DATA_BACKEND: {settings.data_backend}")


dataset_service = get_dataset_repository()
