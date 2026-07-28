from fastapi import APIRouter, HTTPException

from backend.app.db import DatabaseUnavailable
from backend.app.schemas import (
    AssetTreeNode,
    ComponentCreate,
    ComponentInstance,
    VehicleCreate,
    VehicleDetailResponse,
    VehicleInstance,
    VehicleListResponse,
    VehicleSeriesSummary,
    VehicleSystemProfile,
)
from backend.app.services.assets import get_asset_repository

router = APIRouter()


@router.get("/vehicles", response_model=VehicleListResponse)
def list_vehicles(
    page: int = 1,
    page_size: int = 20,
    keyword: str | None = None,
    source_type: str | None = None,
    include_values: bool = False,
) -> VehicleListResponse:
    try:
        return get_asset_repository().search_vehicles(
            page=page,
            page_size=page_size,
            keyword=keyword,
            source_type=source_type,
            include_values=include_values,
        )
    except DatabaseUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/vehicles", response_model=VehicleInstance)
def create_vehicle(payload: VehicleCreate) -> VehicleInstance:
    try:
        return get_asset_repository().create_vehicle(payload)
    except DatabaseUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/vehicles/{vehicle_id}", response_model=VehicleDetailResponse)
def get_vehicle_detail(vehicle_id: int) -> VehicleDetailResponse:
    try:
        return get_asset_repository().get_vehicle_detail(vehicle_id)
    except DatabaseUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/vehicle-series", response_model=list[VehicleSeriesSummary])
def list_vehicle_series(keyword: str | None = None, source_type: str | None = "autohome") -> list[VehicleSeriesSummary]:
    try:
        return get_asset_repository().list_vehicle_series(keyword=keyword, source_type=source_type)
    except DatabaseUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/system-profiles", response_model=list[VehicleSystemProfile])
def list_system_profiles(vehicle_instance_id: int | None = None) -> list[VehicleSystemProfile]:
    try:
        return get_asset_repository().list_system_profiles(vehicle_instance_id)
    except DatabaseUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/components", response_model=list[ComponentInstance])
def list_components(vehicle_instance_id: int | None = None, system_id: int | None = None) -> list[ComponentInstance]:
    try:
        return get_asset_repository().list_components(vehicle_instance_id, system_id)
    except DatabaseUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/components", response_model=ComponentInstance)
def create_component(payload: ComponentCreate) -> ComponentInstance:
    try:
        return get_asset_repository().create_component(payload)
    except DatabaseUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/tree", response_model=list[AssetTreeNode])
def get_asset_tree() -> list[AssetTreeNode]:
    try:
        return get_asset_repository().build_lazy_tree()
    except DatabaseUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/tree/lazy", response_model=list[AssetTreeNode])
def get_lazy_asset_tree(parent_type: str | None = None, parent_id: str | None = None, keyword: str | None = None) -> list[AssetTreeNode]:
    try:
        return get_asset_repository().build_lazy_tree(parent_type=parent_type, parent_id=parent_id, keyword=keyword)
    except DatabaseUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
