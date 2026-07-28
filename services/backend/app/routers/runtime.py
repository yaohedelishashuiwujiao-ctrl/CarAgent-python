from fastapi import APIRouter

from backend.app.schemas_runtime import RuntimeStatus
from backend.app.services.runtime import runtime_service

router = APIRouter()


@router.get("/status", response_model=RuntimeStatus)
def get_status() -> RuntimeStatus:
    return runtime_service.status()
