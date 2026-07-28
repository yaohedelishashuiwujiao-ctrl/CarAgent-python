from __future__ import annotations

import json
from datetime import datetime
from typing import Protocol

from backend.app.db import mysql_connection
from backend.app.repositories.metadata import MemoryMetadataRepository, MySqlMetadataRepository
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


class DatasetRepository(Protocol):
    def list_images(self) -> list[DatasetImage]: ...
    def create_image(self, payload: DatasetImageCreate) -> DatasetImage: ...
    def list_annotations(self, image_id: int | None = None) -> list[DatasetAnnotation]: ...
    def create_annotation(self, payload: DatasetAnnotationCreate) -> DatasetAnnotation: ...
    def summary(self) -> DatasetSummary: ...
    def export_plan(self) -> YoloExportPlan: ...


class MemoryDatasetRepository:
    def __init__(self) -> None:
        from backend.app.services.dataset import InMemoryDatasetState

        self.state = InMemoryDatasetState()

    def list_images(self) -> list[DatasetImage]:
        return self.state.images

    def create_image(self, payload: DatasetImageCreate) -> DatasetImage:
        return self.state.create_image(payload)

    def list_annotations(self, image_id: int | None = None) -> list[DatasetAnnotation]:
        return self.state.list_annotations(image_id)

    def create_annotation(self, payload: DatasetAnnotationCreate) -> DatasetAnnotation:
        return self.state.create_annotation(payload)

    def summary(self) -> DatasetSummary:
        return self.state.summary()

    def export_plan(self) -> YoloExportPlan:
        return self.state.export_plan()


class MySqlDatasetRepository:
    def __init__(self) -> None:
        self.metadata_repository = MySqlMetadataRepository()

    def list_images(self) -> list[DatasetImage]:
        with mysql_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, file_name, source_type, vehicle_hint, system_id, width, height,
                           annotation_status, split, object_count, quality_score, created_at, image_data_url
                    FROM dataset_image
                    ORDER BY id DESC
                    """
                )
                rows = cursor.fetchall()
        return [self._image_from_row(row) for row in rows]

    def create_image(self, payload: DatasetImageCreate) -> DatasetImage:
        with mysql_connection() as connection:
            with connection.cursor() as cursor:
                if payload.system_id is not None:
                    cursor.execute("SELECT id FROM system_catalog WHERE id=%s AND status='active'", (payload.system_id,))
                    if not cursor.fetchone():
                        raise ValueError(f"system does not exist: {payload.system_id}")
                cursor.execute(
                    """
                    INSERT INTO dataset_image
                    (file_name, source_type, vehicle_hint, system_id, width, height, annotation_status,
                     split, object_count, image_data_url)
                    VALUES (%s, %s, %s, %s, %s, %s, 'unlabeled', 'unassigned', 0, %s)
                    """,
                    (
                        payload.file_name,
                        payload.source_type,
                        payload.vehicle_hint,
                        payload.system_id,
                        payload.width,
                        payload.height,
                        payload.image_data_url,
                    ),
                )
                image_id = cursor.lastrowid
        return self._require_image(image_id)

    def list_annotations(self, image_id: int | None = None) -> list[DatasetAnnotation]:
        with mysql_connection() as connection:
            with connection.cursor() as cursor:
                sql = """
                    SELECT a.id, a.image_id, a.entity_type_id, et.code AS entity_type_code,
                           et.name AS entity_type_name, a.bbox_json, a.annotation_type, a.status, a.created_at
                    FROM dataset_annotation a
                    JOIN entity_type et ON et.id = a.entity_type_id
                    WHERE 1=1
                """
                params: tuple = ()
                if image_id:
                    sql += " AND a.image_id=%s"
                    params = (image_id,)
                sql += " ORDER BY a.id"
                cursor.execute(sql, params)
                rows = cursor.fetchall()
        return [self._annotation_from_row(row) for row in rows]

    def create_annotation(self, payload: DatasetAnnotationCreate) -> DatasetAnnotation:
        self._validate_geometry(payload.annotation_type, payload.bbox)
        with mysql_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT id FROM dataset_image WHERE id=%s", (payload.image_id,))
                if not cursor.fetchone():
                    raise ValueError(f"dataset image does not exist: {payload.image_id}")
                cursor.execute("SELECT id, category FROM entity_type WHERE id=%s AND status='active'", (payload.entity_type_id,))
                entity = cursor.fetchone()
                if not entity or entity["category"] != "component":
                    raise ValueError("annotation must use a component entity type")
                cursor.execute(
                    """
                    INSERT INTO dataset_annotation (image_id, entity_type_id, bbox_json, annotation_type, status)
                    VALUES (%s, %s, CAST(%s AS JSON), %s, 'draft')
                    """,
                    (payload.image_id, payload.entity_type_id, json.dumps(payload.bbox), payload.annotation_type),
                )
                annotation_id = cursor.lastrowid
                cursor.execute(
                    """
                    UPDATE dataset_image
                    SET object_count = (SELECT COUNT(*) FROM dataset_annotation WHERE image_id=%s),
                        annotation_status = IF(annotation_status='unlabeled', 'labeling', annotation_status)
                    WHERE id=%s
                    """,
                    (payload.image_id, payload.image_id),
                )
        return next(item for item in self.list_annotations(payload.image_id) if item.id == annotation_id)

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

    def summary(self) -> DatasetSummary:
        images = self.list_images()
        return DatasetSummary(
            image_count=len(images),
            unlabeled_count=sum(1 for item in images if item.annotation_status == "unlabeled"),
            labeling_count=sum(1 for item in images if item.annotation_status == "labeling"),
            reviewed_count=sum(1 for item in images if item.annotation_status == "reviewed"),
            train_count=sum(1 for item in images if item.split == "train"),
            val_count=sum(1 for item in images if item.split == "val"),
            test_count=sum(1 for item in images if item.split == "test"),
            class_stats=self._class_stats(),
        )

    def export_plan(self) -> YoloExportPlan:
        component_types = [item for item in self.metadata_repository.list_entity_types("component")]
        images = self.list_images()
        exportable_images = [item for item in images if self.list_annotations(item.id)]
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
        annotations = self.list_annotations()
        systems = {item.id: item for item in self.metadata_repository.list_systems()}
        stats: list[DatasetClassStat] = []
        for entity in self.metadata_repository.list_entity_types("component"):
            system = systems.get(entity.default_system_id or 0)
            stats.append(
                DatasetClassStat(
                    entity_type_id=entity.id,
                    entity_type_code=entity.code,
                    entity_type_name=entity.name,
                    system_name=system.name if system else None,
                    labeled_instances=sum(1 for item in annotations if item.entity_type_id == entity.id and item.status in {"draft", "reviewed"}),
                    target_instances=300,
                )
            )
        return stats

    def _require_image(self, image_id: int) -> DatasetImage:
        images = [item for item in self.list_images() if item.id == image_id]
        if not images:
            raise ValueError(f"dataset image does not exist: {image_id}")
        return images[0]

    def _image_from_row(self, row: dict) -> DatasetImage:
        return DatasetImage(
            id=row["id"],
            file_name=row["file_name"],
            source_type=row["source_type"],
            vehicle_hint=row["vehicle_hint"],
            system_id=row["system_id"],
            width=row["width"],
            height=row["height"],
            annotation_status=row["annotation_status"],
            split=row["split"],
            object_count=row["object_count"],
            quality_score=float(row["quality_score"]) if row["quality_score"] is not None else None,
            created_at=row["created_at"].isoformat() if row["created_at"] else datetime.now().isoformat(),
            image_data_url=row["image_data_url"],
        )

    def _annotation_from_row(self, row: dict) -> DatasetAnnotation:
        bbox = row["bbox_json"]
        if isinstance(bbox, str):
            bbox = json.loads(bbox)
        return DatasetAnnotation(
            id=row["id"],
            image_id=row["image_id"],
            entity_type_id=row["entity_type_id"],
            entity_type_code=row["entity_type_code"],
            entity_type_name=row["entity_type_name"],
            bbox=[float(value) for value in bbox],
            annotation_type=row["annotation_type"],
            status=row["status"],
            created_at=row["created_at"].isoformat() if row["created_at"] else datetime.now().isoformat(),
        )
