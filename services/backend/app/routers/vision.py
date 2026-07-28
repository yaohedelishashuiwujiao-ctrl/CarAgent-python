from fastapi import APIRouter, HTTPException

from backend.app.db import DatabaseUnavailable
from backend.app.schemas_vision import VisionAnalyzeRequest, VisionAnalyzeResponse, VisionRefineRequest, VisionRefineResponse, VisionTask
from backend.app.services.vision import vision_service

router = APIRouter()


@router.post("/analyze", response_model=VisionAnalyzeResponse)
def analyze_image(payload: VisionAnalyzeRequest) -> VisionAnalyzeResponse:
    try:
        return vision_service.analyze(payload)
    except DatabaseUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/refine", response_model=VisionRefineResponse)
def refine_region(payload: VisionRefineRequest) -> VisionRefineResponse:
    try:
        return vision_service.refine(payload)
    except DatabaseUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/tasks", response_model=list[VisionTask])
def list_tasks() -> list[VisionTask]:
    try:
        return vision_service.tasks
    except DatabaseUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
