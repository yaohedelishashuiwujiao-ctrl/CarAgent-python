from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
import os

from backend.app.db import DatabaseUnavailable
from backend.app.schemas_dataset import (
    DatasetAnnotation,
    DatasetAnnotationCreate,
    DatasetImage,
    DatasetImageCreate,
    DatasetSummary,
    YoloExportPlan,
)
from backend.app.services.dataset import dataset_service
from backend.app.services.dataset_export import build_coco_seg_export, build_yolo_seg_export
from backend.app.services.metadata import get_metadata_repository

router = APIRouter()


@router.get("/images", response_model=list[DatasetImage])
def list_images() -> list[DatasetImage]:
    try:
        return dataset_service.list_images()
    except DatabaseUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/images", response_model=DatasetImage)
def create_image(payload: DatasetImageCreate) -> DatasetImage:
    try:
        return dataset_service.create_image(payload)
    except DatabaseUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/annotations", response_model=list[DatasetAnnotation])
def list_annotations(image_id: int | None = None) -> list[DatasetAnnotation]:
    try:
        return dataset_service.list_annotations(image_id)
    except DatabaseUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/annotations", response_model=DatasetAnnotation)
def create_annotation(payload: DatasetAnnotationCreate) -> DatasetAnnotation:
    try:
        return dataset_service.create_annotation(payload)
    except DatabaseUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/summary", response_model=DatasetSummary)
def get_summary() -> DatasetSummary:
    try:
        return dataset_service.summary()
    except DatabaseUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/exports/yolo-plan", response_model=YoloExportPlan)
def get_yolo_export_plan() -> YoloExportPlan:
    try:
        return dataset_service.export_plan()
    except DatabaseUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/exports/yolo-seg.zip")
def download_yolo_seg_export() -> FileResponse:
    try:
        artifact = build_yolo_seg_export(dataset_service, get_metadata_repository())
    except DatabaseUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return FileResponse(
        artifact.path,
        media_type="application/zip",
        filename=f"{artifact.export_name}.zip",
        background=BackgroundTask(os.remove, artifact.path),
    )


@router.get("/exports/coco-seg.zip")
def download_coco_seg_export() -> FileResponse:
    try:
        artifact = build_coco_seg_export(dataset_service, get_metadata_repository())
    except DatabaseUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return FileResponse(
        artifact.path,
        media_type="application/zip",
        filename=f"{artifact.export_name}.zip",
        background=BackgroundTask(os.remove, artifact.path),
    )
