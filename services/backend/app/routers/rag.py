from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from backend.app.security import Principal, get_data_principal
from backend.app.services.rag import RagDependencyError, rag_service


router = APIRouter()


@router.get("/status")
def status() -> dict[str, Any]:
    try:
        return rag_service.status()
    except RagDependencyError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/index/rebuild")
def rebuild_index(principal: Principal = Depends(get_data_principal)) -> dict[str, Any]:
    _require_admin(principal)
    try:
        return rag_service.rebuild()
    except RagDependencyError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/search")
def search(payload: dict[str, Any], principal: Principal = Depends(get_data_principal)) -> dict[str, Any]:
    try:
        query = str(payload.get("query") or "")
        top_k = int(payload.get("top_k") or 8)
        document_ids = payload.get("document_ids")
        metadata = payload.get("metadata")
        if document_ids is not None and not isinstance(document_ids, list):
            raise ValueError("document_ids must be a list")
        if metadata is not None and not isinstance(metadata, dict):
            raise ValueError("metadata must be an object")
        return rag_service.search(
            query,
            top_k=top_k,
            document_ids=document_ids,
            metadata=metadata,
            data_scope=principal.data_scope,
        )
    except RagDependencyError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/evaluate")
def evaluate(payload: dict[str, Any], principal: Principal = Depends(get_data_principal)) -> dict[str, Any]:
    _require_admin(principal)
    try:
        cases = payload.get("cases")
        if not isinstance(cases, list):
            raise ValueError("cases must be a list")
        return rag_service.evaluate(cases)
    except RagDependencyError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/chunks/{chunk_id}")
def fetch_chunk(chunk_id: str, principal: Principal = Depends(get_data_principal)) -> dict[str, Any]:
    try:
        chunk = rag_service.fetch(chunk_id, data_scope=principal.data_scope)
    except RagDependencyError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if chunk is None:
        raise HTTPException(status_code=404, detail="chunk not found")
    return chunk


def _require_admin(principal: Principal) -> None:
    if not principal.is_admin:
        raise HTTPException(status_code=403, detail="administrator role is required")
