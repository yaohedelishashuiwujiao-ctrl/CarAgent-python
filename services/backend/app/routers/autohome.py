from fastapi import APIRouter, HTTPException

from backend.app.db import DatabaseUnavailable
from backend.app.schemas_autohome import AutohomeImportRequest, AutohomeImportResponse, AutohomeScanRequest, AutohomeScanResponse
from backend.app.services.autohome import autohome_service

router = APIRouter()


@router.post("/scan", response_model=AutohomeScanResponse)
def scan_autohome_dataset(payload: AutohomeScanRequest) -> AutohomeScanResponse:
    try:
        return autohome_service.scan(payload)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DatabaseUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/import", response_model=AutohomeImportResponse)
def import_autohome_dataset(payload: AutohomeImportRequest) -> AutohomeImportResponse:
    try:
        return autohome_service.import_dataset(payload)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DatabaseUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
