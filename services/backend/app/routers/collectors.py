from fastapi import APIRouter, HTTPException

from backend.app.db import DatabaseUnavailable
from backend.app.schemas_collectors import CollectorTask, CollectorTaskCreate
from backend.app.services.collectors import collector_service

router = APIRouter()


@router.get("/tasks", response_model=list[CollectorTask])
def list_tasks() -> list[CollectorTask]:
    try:
        return collector_service.list_tasks()
    except DatabaseUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/tasks", response_model=CollectorTask)
def create_task(payload: CollectorTaskCreate) -> CollectorTask:
    try:
        return collector_service.create_task(payload)
    except DatabaseUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
