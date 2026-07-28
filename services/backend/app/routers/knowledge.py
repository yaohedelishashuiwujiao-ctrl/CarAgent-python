from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from backend.app.schemas_knowledge import KnowledgeSearchResponse, KnowledgeWorkspaceStatus
from backend.app.services.knowledge import knowledge_service

router = APIRouter()


@router.get("/status", response_model=KnowledgeWorkspaceStatus)
def get_status() -> KnowledgeWorkspaceStatus:
    return knowledge_service.status()


@router.get("/test", response_model=KnowledgeSearchResponse)
def test_search(query: str = Query(..., min_length=1), top_k: int = Query(5, ge=1, le=20)) -> KnowledgeSearchResponse:
    try:
        return knowledge_service.search(query, top_k=top_k)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
