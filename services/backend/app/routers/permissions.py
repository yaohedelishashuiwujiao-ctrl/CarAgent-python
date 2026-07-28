from fastapi import APIRouter, HTTPException

from backend.app.db import DatabaseUnavailable
from backend.app.schemas import Permission, Role
from backend.app.services.permissions import get_permission_repository

router = APIRouter()


@router.get("/roles", response_model=list[Role])
def list_roles() -> list[Role]:
    try:
        return get_permission_repository().list_roles()
    except DatabaseUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("", response_model=list[Permission])
def list_permissions() -> list[Permission]:
    try:
        return get_permission_repository().list_permissions()
    except DatabaseUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
