from fastapi import APIRouter, HTTPException

from backend.app.db import DatabaseUnavailable
from backend.app.schemas import (
    Attribute,
    AttributeCreate,
    AttributeGroup,
    AttributeUpdate,
    EntityType,
    EntityTypeCreate,
    EntityTypeUpdate,
    SystemCatalog,
)
from backend.app.services.metadata import get_metadata_repository

router = APIRouter()


@router.get("/entity-types", response_model=list[EntityType])
def list_entity_types(category: str | None = None) -> list[EntityType]:
    try:
        return get_metadata_repository().list_entity_types(category)
    except DatabaseUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/entity-types", response_model=EntityType)
def create_entity_type(payload: EntityTypeCreate) -> EntityType:
    try:
        return get_metadata_repository().create_entity_type(payload)
    except DatabaseUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/entity-types/{entity_type_id}", response_model=EntityType)
def update_entity_type(entity_type_id: int, payload: EntityTypeUpdate) -> EntityType:
    try:
        return get_metadata_repository().update_entity_type(entity_type_id, payload)
    except DatabaseUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/object-types", response_model=list[EntityType])
def list_object_types_compat(category: str | None = None) -> list[EntityType]:
    return list_entity_types(category)


@router.get("/systems", response_model=list[SystemCatalog])
def list_systems() -> list[SystemCatalog]:
    try:
        return get_metadata_repository().list_systems()
    except DatabaseUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/attribute-groups", response_model=list[AttributeGroup])
def list_attribute_groups(entity_type_id: int | None = None) -> list[AttributeGroup]:
    try:
        return get_metadata_repository().list_attribute_groups(entity_type_id)
    except DatabaseUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/attributes", response_model=list[Attribute])
def list_attributes(entity_type_id: int | None = None) -> list[Attribute]:
    try:
        return get_metadata_repository().list_attributes(entity_type_id)
    except DatabaseUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/attributes", response_model=Attribute)
def create_attribute(payload: AttributeCreate) -> Attribute:
    try:
        return get_metadata_repository().create_attribute(payload)
    except DatabaseUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/attributes/{attribute_id}", response_model=Attribute)
def update_attribute(attribute_id: int, payload: AttributeUpdate) -> Attribute:
    try:
        return get_metadata_repository().update_attribute(attribute_id, payload)
    except DatabaseUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
