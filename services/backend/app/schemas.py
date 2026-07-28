from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


AttributeType = Literal[
    "text",
    "long_text",
    "number",
    "integer",
    "enum",
    "multi_enum",
    "date",
    "datetime",
    "boolean",
    "image",
    "file",
    "json",
    "relation",
]

EntityCategory = Literal["vehicle", "component"]
InstanceTargetType = Literal["vehicle", "component", "system_profile"]


class EntityType(BaseModel):
    id: int
    category: EntityCategory
    code: str
    name: str
    description: str | None = None
    is_builtin: bool = False
    status: str = "active"
    sort_order: int = 0
    default_system_id: int | None = None


class EntityTypeCreate(BaseModel):
    category: EntityCategory = "component"
    code: str
    name: str
    description: str | None = None
    default_system_id: int | None = None
    sort_order: int = 0


class EntityTypeUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    default_system_id: int | None = None
    status: str | None = None


class SystemCatalog(BaseModel):
    id: int
    code: str
    name: str
    description: str | None = None
    is_builtin: bool = True
    status: str = "active"
    sort_order: int = 0


class AttributeGroup(BaseModel):
    id: int
    entity_type_id: int
    code: str
    name: str
    sort_order: int = 0


class AttributeOption(BaseModel):
    value: str
    label: str
    sort_order: int = 0


class Attribute(BaseModel):
    id: int
    entity_type_id: int
    group_id: int | None = None
    code: str
    name: str
    attr_type: AttributeType
    unit: str | None = None
    is_required: bool = False
    is_searchable: bool = False
    is_importable: bool = True
    is_exportable: bool = True
    is_multi_value: bool = False
    options: list[AttributeOption] = Field(default_factory=list)
    config: dict[str, Any] = Field(default_factory=dict)
    sort_order: int = 0


class AttributeCreate(BaseModel):
    entity_type_id: int
    group_id: int | None = None
    code: str
    name: str
    attr_type: AttributeType
    unit: str | None = None
    is_required: bool = False
    is_searchable: bool = False
    is_importable: bool = True
    is_exportable: bool = True
    is_multi_value: bool = False
    options: list[AttributeOption] = Field(default_factory=list)
    config: dict[str, Any] = Field(default_factory=dict)
    sort_order: int = 0


class AttributeUpdate(BaseModel):
    name: str | None = None
    attr_type: AttributeType | None = None
    unit: str | None = None
    is_required: bool | None = None
    is_searchable: bool | None = None
    is_importable: bool | None = None
    is_exportable: bool | None = None
    is_multi_value: bool | None = None
    status: str | None = None


class InstanceValue(BaseModel):
    attribute_id: int
    attribute_code: str
    value: Any
    unit: str | None = None
    source: str = "manual"
    confidence: float | None = None


class VehicleInstance(BaseModel):
    id: int
    entity_type_id: int
    vehicle_code: str
    vehicle_name: str
    source_type: str = "manual"
    status: str = "active"
    values: list[InstanceValue] = Field(default_factory=list)


class VehicleListResponse(BaseModel):
    items: list[VehicleInstance]
    total: int
    page: int
    page_size: int
    source_counts: dict[str, int] = Field(default_factory=dict)


class VehicleSeriesSummary(BaseModel):
    series_id: str
    series_name: str
    source_type: str = "autohome"
    spec_count: int


class AttributeValueDetail(BaseModel):
    attribute_id: int
    attribute_code: str
    attribute_name: str
    attr_type: str
    unit: str | None = None
    value: Any
    source: str = "manual"
    confidence: float | None = None


class AttributeValueGroup(BaseModel):
    group_id: int | None = None
    group_code: str = "ungrouped"
    group_name: str = "未分组"
    values: list[AttributeValueDetail] = Field(default_factory=list)


class VehicleDetailResponse(BaseModel):
    vehicle: VehicleInstance
    groups: list[AttributeValueGroup] = Field(default_factory=list)


class VehicleCreate(BaseModel):
    vehicle_code: str
    vehicle_name: str
    source_type: str = "manual"
    values: list[InstanceValue] = Field(default_factory=list)


class ComponentInstance(BaseModel):
    id: int
    vehicle_instance_id: int
    system_id: int
    entity_type_id: int
    component_code: str
    component_name: str
    source_type: str = "manual"
    status: str = "active"
    values: list[InstanceValue] = Field(default_factory=list)


class ComponentCreate(BaseModel):
    vehicle_instance_id: int
    system_id: int
    entity_type_id: int
    component_code: str
    component_name: str
    source_type: str = "manual"
    values: list[InstanceValue] = Field(default_factory=list)


class VehicleSystemProfile(BaseModel):
    id: int
    vehicle_instance_id: int
    system_id: int
    profile_name: str
    values: list[InstanceValue] = Field(default_factory=list)


class AssetTreeNode(BaseModel):
    id: int
    node_type: Literal["vehicle_series", "vehicle", "system_profile", "component"]
    title: str
    code: str
    entity_type_id: int | None = None
    system_id: int | None = None
    instance_id: int | None = None
    children: list["AssetTreeNode"] = Field(default_factory=list)


class Permission(BaseModel):
    code: str
    name: str
    resource_type: str
    action: str


class Role(BaseModel):
    code: str
    name: str
    data_scope: str
    permissions: list[str] = Field(default_factory=list)


AssetTreeNode.model_rebuild()
