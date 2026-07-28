from fastapi import APIRouter, HTTPException

from backend.app.db import DatabaseUnavailable
from backend.app.schemas_evidence import EvidenceCreate, EvidenceItem, EvidenceSummary
from backend.app.services.evidence import evidence_service

router = APIRouter()


@router.get("/items", response_model=list[EvidenceItem])
def list_items(
    evidence_type: str | None = None,
    source_type: str | None = None,
    review_status: str | None = None,
) -> list[EvidenceItem]:
    try:
        return evidence_service.list_items(evidence_type=evidence_type, source_type=source_type, review_status=review_status)
    except DatabaseUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/items", response_model=EvidenceItem)
def create_item(payload: EvidenceCreate) -> EvidenceItem:
    try:
        return evidence_service.create_item(payload)
    except DatabaseUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/summary", response_model=EvidenceSummary)
def get_summary() -> EvidenceSummary:
    try:
        return evidence_service.summary()
    except DatabaseUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
