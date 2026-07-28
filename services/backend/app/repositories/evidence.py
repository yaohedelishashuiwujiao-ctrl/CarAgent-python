from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from typing import Protocol

from backend.app.db import mysql_connection
from backend.app.repositories.assets import MemoryAssetRepository, MySqlAssetRepository
from backend.app.repositories.metadata import MemoryMetadataRepository, MySqlMetadataRepository
from backend.app.schemas_evidence import EvidenceCreate, EvidenceItem, EvidenceSummary
from backend.app.services.dataset import dataset_service
from backend.app.services.vision import vision_service


class EvidenceRepository(Protocol):
    def list_items(
        self,
        evidence_type: str | None = None,
        source_type: str | None = None,
        review_status: str | None = None,
    ) -> list[EvidenceItem]: ...
    def create_item(self, payload: EvidenceCreate) -> EvidenceItem: ...
    def summary(self) -> EvidenceSummary: ...


class BaseEvidenceRepository:
    asset_repository: MemoryAssetRepository | MySqlAssetRepository
    metadata_repository: MemoryMetadataRepository | MySqlMetadataRepository

    def summary(self) -> EvidenceSummary:
        items = self.list_items()
        source_counts = Counter(item.source_type for item in items)
        type_counts = Counter(item.evidence_type for item in items)
        return EvidenceSummary(
            total_count=len(items),
            reviewed_count=sum(1 for item in items if item.review_status == "reviewed"),
            candidate_count=sum(1 for item in items if item.review_status in {"candidate", "needs_review", "draft"}),
            rejected_count=sum(1 for item in items if item.review_status == "rejected"),
            low_confidence_count=sum(1 for item in items if item.confidence is not None and item.confidence < 0.75),
            source_counts=dict(source_counts),
            type_counts=dict(type_counts),
        )

    def _projected_items(self) -> list[EvidenceItem]:
        items: list[EvidenceItem] = []
        systems = {item.id: item for item in self.metadata_repository.list_systems()}
        entity_types = {item.id: item for item in self.metadata_repository.list_entity_types()}
        vehicles = self.asset_repository.list_vehicles()
        vehicles_by_id = {item.id: item for item in vehicles}

        for vehicle in vehicles:
            values = ", ".join(f"{item.attribute_code}={item.value}{item.unit or ''}" for item in vehicle.values) or "暂无动态属性"
            items.append(
                EvidenceItem(
                    id=f"vehicle:{vehicle.id}",
                    title=vehicle.vehicle_name,
                    content=f"整车实例 {vehicle.vehicle_name}，业务编码 {vehicle.vehicle_code}，动态属性：{values}",
                    evidence_type="structured_vehicle",
                    source_type=vehicle.source_type,
                    source_ref=vehicle.vehicle_code,
                    confidence=1.0,
                    review_status="reviewed" if vehicle.status == "active" else vehicle.status,
                    vehicle_instance_id=vehicle.id,
                    entity_type_id=vehicle.entity_type_id,
                    metadata={"vehicle_code": vehicle.vehicle_code},
                )
            )

        for component in self.asset_repository.list_components():
            vehicle = vehicles_by_id.get(component.vehicle_instance_id)
            system = systems.get(component.system_id)
            entity = entity_types.get(component.entity_type_id)
            values = ", ".join(f"{item.attribute_code}={item.value}{item.unit or ''}" for item in component.values) or "暂无动态属性"
            items.append(
                EvidenceItem(
                    id=f"component:{component.id}",
                    title=component.component_name,
                    content=(
                        f"车型={vehicle.vehicle_name if vehicle else '-'}，系统={system.name if system else '-'}，"
                        f"零部件实体={entity.name if entity else '-'}，实例={component.component_name}，动态属性：{values}"
                    ),
                    evidence_type="structured_component",
                    source_type=component.source_type,
                    source_ref=component.component_code,
                    confidence=1.0,
                    review_status="reviewed" if component.status == "active" else component.status,
                    vehicle_instance_id=component.vehicle_instance_id,
                    system_id=component.system_id,
                    entity_type_id=component.entity_type_id,
                    metadata={"component_code": component.component_code},
                )
            )

        dataset_images = dataset_service.list_images()
        dataset_annotations = dataset_service.list_annotations()

        for image in dataset_images:
            system = systems.get(image.system_id or 0)
            status = "reviewed" if image.annotation_status == "reviewed" else "candidate"
            items.append(
                EvidenceItem(
                    id=f"dataset_image:{image.id}",
                    title=image.file_name,
                    content=(
                        f"图片 {image.file_name}，来源={image.source_type}，车型线索={image.vehicle_hint or '-'}，"
                        f"系统线索={system.name if system else '-'}，标注状态={image.annotation_status}，"
                        f"对象数={image.object_count}，数据集划分={image.split}"
                    ),
                    evidence_type="dataset_image",
                    source_type=image.source_type,
                    source_ref=image.file_name,
                    confidence=image.quality_score,
                    review_status=status,
                    system_id=image.system_id,
                    metadata={"image_id": image.id, "split": image.split},
                    created_at=image.created_at,
                )
            )

        for annotation in dataset_annotations:
            image = next((item for item in dataset_images if item.id == annotation.image_id), None)
            items.append(
                EvidenceItem(
                    id=f"annotation:{annotation.id}",
                    title=f"{annotation.entity_type_name} 标注",
                    content=(
                        f"图片={image.file_name if image else annotation.image_id}，标注零部件={annotation.entity_type_name}，"
                        f"bbox={annotation.bbox}，状态={annotation.status}"
                    ),
                    evidence_type="dataset_annotation",
                    source_type="manual_labeling",
                    source_ref=str(annotation.image_id),
                    confidence=0.95 if annotation.status == "reviewed" else 0.7,
                    review_status=annotation.status,
                    system_id=image.system_id if image else None,
                    entity_type_id=annotation.entity_type_id,
                    metadata={"image_id": annotation.image_id, "bbox": annotation.bbox},
                    created_at=annotation.created_at,
                )
            )

        for task in vision_service.tasks:
            items.append(
                EvidenceItem(
                    id=f"vision_task:{task.id}",
                    title=f"视觉识别任务 {task.file_name}",
                    content=f"视觉识别任务，文件={task.file_name}，识别对象数={task.object_count}，说明={task.ai_summary}",
                    evidence_type="vision_detection",
                    source_type=task.detector_name,
                    source_ref=task.file_name,
                    confidence=0.6,
                    review_status="needs_review",
                    metadata={"task_id": task.id},
                )
            )
        return items

    def _apply_filters(
        self,
        items: list[EvidenceItem],
        evidence_type: str | None,
        source_type: str | None,
        review_status: str | None,
    ) -> list[EvidenceItem]:
        if evidence_type:
            items = [item for item in items if item.evidence_type == evidence_type]
        if source_type:
            items = [item for item in items if item.source_type == source_type]
        if review_status:
            items = [item for item in items if item.review_status == review_status]
        return items


class MemoryEvidenceRepository(BaseEvidenceRepository):
    def __init__(self) -> None:
        self.asset_repository = MemoryAssetRepository()
        self.metadata_repository = MemoryMetadataRepository()
        self.manual_items: list[EvidenceItem] = []

    def list_items(
        self,
        evidence_type: str | None = None,
        source_type: str | None = None,
        review_status: str | None = None,
    ) -> list[EvidenceItem]:
        return self._apply_filters(self._projected_items() + self.manual_items, evidence_type, source_type, review_status)

    def create_item(self, payload: EvidenceCreate) -> EvidenceItem:
        item = EvidenceItem(
            id=f"manual:{len(self.manual_items) + 1}",
            created_at=datetime.now().replace(microsecond=0).isoformat(),
            **payload.model_dump(),
        )
        self.manual_items.insert(0, item)
        return item


class MySqlEvidenceRepository(BaseEvidenceRepository):
    def __init__(self) -> None:
        self.asset_repository = MySqlAssetRepository()
        self.metadata_repository = MySqlMetadataRepository()

    def list_items(
        self,
        evidence_type: str | None = None,
        source_type: str | None = None,
        review_status: str | None = None,
    ) -> list[EvidenceItem]:
        items = self._projected_items() + self._stored_items()
        return self._apply_filters(items, evidence_type, source_type, review_status)

    def create_item(self, payload: EvidenceCreate) -> EvidenceItem:
        with mysql_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO evidence_item
                    (evidence_type, title, content, source_type, source_ref, confidence,
                     review_status, vehicle_instance_id, system_id, entity_type_id, metadata_json)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CAST(%s AS JSON))
                    """,
                    (
                        payload.evidence_type,
                        payload.title,
                        payload.content,
                        payload.source_type,
                        payload.source_ref,
                        payload.confidence,
                        payload.review_status,
                        payload.vehicle_instance_id,
                        payload.system_id,
                        payload.entity_type_id,
                        json.dumps(payload.metadata, ensure_ascii=False),
                    ),
                )
                evidence_id = cursor.lastrowid
        return self._require_stored_item(evidence_id)

    def _stored_items(self) -> list[EvidenceItem]:
        with mysql_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, evidence_type, title, content, source_type, source_ref, confidence,
                           review_status, vehicle_instance_id, system_id, entity_type_id,
                           metadata_json, created_at
                    FROM evidence_item
                    ORDER BY id DESC
                    """
                )
                rows = cursor.fetchall()
        return [self._item_from_row(row) for row in rows]

    def _require_stored_item(self, evidence_id: int) -> EvidenceItem:
        with mysql_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, evidence_type, title, content, source_type, source_ref, confidence,
                           review_status, vehicle_instance_id, system_id, entity_type_id,
                           metadata_json, created_at
                    FROM evidence_item
                    WHERE id=%s
                    """,
                    (evidence_id,),
                )
                row = cursor.fetchone()
        if not row:
            raise ValueError(f"evidence item does not exist: {evidence_id}")
        return self._item_from_row(row)

    def _item_from_row(self, row: dict) -> EvidenceItem:
        metadata = row["metadata_json"] or {}
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
        return EvidenceItem(
            id=f"evidence:{row['id']}",
            title=row["title"],
            content=row["content"],
            evidence_type=row["evidence_type"],
            source_type=row["source_type"],
            source_ref=row["source_ref"],
            confidence=float(row["confidence"]) if row["confidence"] is not None else None,
            review_status=row["review_status"],
            vehicle_instance_id=row["vehicle_instance_id"],
            system_id=row["system_id"],
            entity_type_id=row["entity_type_id"],
            metadata=metadata,
            created_at=row["created_at"].isoformat() if row["created_at"] is not None else None,
        )
