from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from backend.app.db import DatabaseUnavailable
from backend.app.services.metadata import get_metadata_repository

router = APIRouter()


class ImportTemplate(BaseModel):
    template_type: str
    name: str
    description: str
    fixed_columns: list[str]
    dynamic_columns: list[str]


@router.get("/templates", response_model=list[ImportTemplate])
def list_import_templates() -> list[ImportTemplate]:
    try:
        metadata = get_metadata_repository()
        vehicle_type = next((item for item in metadata.list_entity_types("vehicle") if item.code == "vehicle"), None)
        first_component = next(iter(metadata.list_entity_types("component")), None)
        vehicle_attrs = metadata.list_attributes(vehicle_type.id) if vehicle_type else []
        component_attrs = metadata.list_attributes(first_component.id) if first_component else []
    except DatabaseUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return [
        ImportTemplate(
            template_type="vehicle_instance",
            name="整车实例导入模板",
            description="批量创建车型实例，并填写整车动态属性。",
            fixed_columns=["vehicle_code", "vehicle_name"],
            dynamic_columns=[_attribute_column(attr) for attr in vehicle_attrs],
        ),
        ImportTemplate(
            template_type="component_entity_type",
            name="零部件实体类型导入模板",
            description="批量维护零部件实体字典及默认虚拟系统。",
            fixed_columns=["entity_type_code", "entity_type_name", "default_system_code", "description"],
            dynamic_columns=[],
        ),
        ImportTemplate(
            template_type="component_instance",
            name="零部件实例导入模板",
            description="在指定整车和系统下批量导入零部件实例及属性值。",
            fixed_columns=["vehicle_code", "system_code", "entity_type_code", "component_code", "component_name"],
            dynamic_columns=[_attribute_column(attr) for attr in component_attrs],
        ),
    ]


@router.get("/templates/{template_type}/csv", response_class=PlainTextResponse)
def download_template_csv(template_type: str) -> str:
    template = next((item for item in list_import_templates() if item.template_type == template_type), None)
    if template is None:
        return "unknown_template\n"
    columns = template.fixed_columns + template.dynamic_columns
    return ",".join(columns) + "\n"


def _attribute_column(attribute) -> str:
    unit = f"|{attribute.unit}" if attribute.unit else ""
    return f"{attribute.name}[{attribute.code}|{attribute.attr_type}{unit}]"
