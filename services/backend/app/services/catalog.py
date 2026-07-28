from __future__ import annotations

from backend.app.schemas import (
    AssetTreeNode,
    Attribute,
    AttributeCreate,
    AttributeUpdate,
    AttributeGroup,
    ComponentCreate,
    ComponentInstance,
    EntityTypeCreate,
    EntityType,
    EntityTypeUpdate,
    InstanceValue,
    Permission,
    Role,
    SystemCatalog,
    VehicleCreate,
    VehicleInstance,
    VehicleSystemProfile,
)


class CatalogStore:
    def __init__(self) -> None:
        self.systems = [
            SystemCatalog(id=1, code="suspension", name="悬架系统", description="虚拟系统节点，用于组织悬架零部件", sort_order=10),
            SystemCatalog(id=2, code="braking", name="制动系统", description="虚拟系统节点，用于组织制动零部件", sort_order=20),
            SystemCatalog(id=3, code="steering", name="转向系统", description="虚拟系统节点，用于组织转向零部件", sort_order=30),
            SystemCatalog(id=4, code="powertrain", name="动力系统", description="虚拟系统节点，用于组织动力零部件", sort_order=40),
        ]
        self.entity_types = [
            EntityType(id=1, category="vehicle", code="vehicle", name="整车", description="整车实体类型", is_builtin=True, sort_order=10),
            EntityType(id=2, category="component", code="upper_control_arm", name="上控制臂/上摆臂", description="悬架零部件实体类型，位置由实例属性表达", is_builtin=True, sort_order=100, default_system_id=1),
            EntityType(id=3, category="component", code="lower_control_arm", name="下控制臂/下摆臂", description="悬架零部件实体类型，位置由实例属性表达", is_builtin=True, sort_order=105, default_system_id=1),
            EntityType(id=4, category="component", code="front_subframe", name="前副车架", description="悬架零部件实体类型", is_builtin=True, sort_order=110, default_system_id=1),
            EntityType(id=5, category="component", code="brake_disc", name="制动盘", description="制动零部件实体类型", is_builtin=True, sort_order=200, default_system_id=2),
            EntityType(id=6, category="component", code="brake_caliper", name="制动卡钳", description="制动零部件实体类型", is_builtin=True, sort_order=210, default_system_id=2),
            EntityType(id=7, category="component", code="steering_knuckle", name="转向节", description="转向零部件实体类型", is_builtin=True, sort_order=300, default_system_id=3),
            EntityType(id=8, category="component", code="tie_rod", name="转向拉杆", description="转向零部件实体类型", is_builtin=True, sort_order=310, default_system_id=3),
            EntityType(id=9, category="component", code="drive_shaft", name="半轴", description="动力零部件实体类型", is_builtin=True, sort_order=400, default_system_id=4),
        ]
        self.attribute_groups = [
            AttributeGroup(id=1, entity_type_id=1, code="basic", name="基本信息", sort_order=10),
            AttributeGroup(id=2, entity_type_id=1, code="dimension", name="尺寸参数", sort_order=20),
            AttributeGroup(id=3, entity_type_id=2, code="basic", name="零部件信息", sort_order=10),
            AttributeGroup(id=4, entity_type_id=2, code="material", name="材料与工艺", sort_order=20),
            AttributeGroup(id=5, entity_type_id=3, code="basic", name="零部件信息", sort_order=10),
            AttributeGroup(id=6, entity_type_id=3, code="material", name="材料与工艺", sort_order=20),
            AttributeGroup(id=7, entity_type_id=4, code="basic", name="零部件信息", sort_order=10),
            AttributeGroup(id=8, entity_type_id=4, code="material", name="材料与工艺", sort_order=20),
            AttributeGroup(id=9, entity_type_id=5, code="basic", name="零部件信息", sort_order=10),
            AttributeGroup(id=10, entity_type_id=5, code="material", name="材料与工艺", sort_order=20),
            AttributeGroup(id=11, entity_type_id=6, code="basic", name="零部件信息", sort_order=10),
            AttributeGroup(id=12, entity_type_id=6, code="material", name="材料与工艺", sort_order=20),
            AttributeGroup(id=13, entity_type_id=7, code="basic", name="零部件信息", sort_order=10),
            AttributeGroup(id=14, entity_type_id=7, code="material", name="材料与工艺", sort_order=20),
            AttributeGroup(id=15, entity_type_id=8, code="basic", name="零部件信息", sort_order=10),
            AttributeGroup(id=16, entity_type_id=8, code="material", name="材料与工艺", sort_order=20),
            AttributeGroup(id=17, entity_type_id=9, code="basic", name="零部件信息", sort_order=10),
            AttributeGroup(id=18, entity_type_id=9, code="material", name="材料与工艺", sort_order=20),
        ]
        self.attributes: list[Attribute] = [
            Attribute(id=1, entity_type_id=1, group_id=1, code="brand", name="品牌", attr_type="text", is_required=True, is_searchable=True, sort_order=10),
            Attribute(id=2, entity_type_id=1, group_id=1, code="model_name", name="车型名称", attr_type="text", is_required=True, is_searchable=True, sort_order=20),
            Attribute(id=3, entity_type_id=1, group_id=2, code="wheelbase", name="轴距", attr_type="number", unit="mm", is_searchable=True, sort_order=30),
            Attribute(id=4, entity_type_id=1, group_id=2, code="curb_weight", name="整备质量", attr_type="number", unit="kg", sort_order=40),
            Attribute(id=5, entity_type_id=2, group_id=3, code="position", name="安装位置", attr_type="text", is_searchable=True, sort_order=10),
            Attribute(id=6, entity_type_id=2, group_id=3, code="weight", name="重量", attr_type="number", unit="kg", sort_order=20),
            Attribute(id=7, entity_type_id=2, group_id=4, code="material", name="材料", attr_type="text", is_searchable=True, sort_order=30),
            Attribute(id=8, entity_type_id=2, group_id=4, code="manufacturing", name="工艺", attr_type="text", sort_order=40),
            Attribute(id=9, entity_type_id=5, group_id=9, code="diameter", name="直径", attr_type="number", unit="mm", sort_order=10),
            Attribute(id=10, entity_type_id=7, group_id=14, code="material", name="材料", attr_type="text", sort_order=10),
        ]
        self.vehicles = [
            VehicleInstance(
                id=1,
                entity_type_id=1,
                vehicle_code="XPENG_X9",
                vehicle_name="小鹏 X9",
                values=[
                    InstanceValue(attribute_id=1, attribute_code="brand", value="小鹏"),
                    InstanceValue(attribute_id=2, attribute_code="model_name", value="X9"),
                    InstanceValue(attribute_id=3, attribute_code="wheelbase", value=3160, unit="mm"),
                ],
            )
        ]
        self.system_profiles = [
            VehicleSystemProfile(id=1, vehicle_instance_id=1, system_id=1, profile_name="小鹏 X9 / 悬架系统"),
            VehicleSystemProfile(id=2, vehicle_instance_id=1, system_id=2, profile_name="小鹏 X9 / 制动系统"),
            VehicleSystemProfile(id=3, vehicle_instance_id=1, system_id=3, profile_name="小鹏 X9 / 转向系统"),
            VehicleSystemProfile(id=4, vehicle_instance_id=1, system_id=4, profile_name="小鹏 X9 / 动力系统"),
        ]
        self.components: list[ComponentInstance] = [
            ComponentInstance(
                id=1,
                vehicle_instance_id=1,
                system_id=1,
                entity_type_id=2,
                component_code="XPENG_X9_LF_UPPER_ARM",
                component_name="小鹏 X9 左前上摆臂",
                values=[
                    InstanceValue(attribute_id=5, attribute_code="position", value="左前"),
                    InstanceValue(attribute_id=6, attribute_code="weight", value=2.8, unit="kg"),
                    InstanceValue(attribute_id=7, attribute_code="material", value="铝合金"),
                ],
            )
        ]
        self.permissions = [
            Permission(code="dashboard:read", name="查看首页", resource_type="dashboard", action="read"),
            Permission(code="metadata:read", name="查看元数据", resource_type="metadata", action="read"),
            Permission(code="metadata:update", name="维护元数据", resource_type="metadata", action="update"),
            Permission(code="asset:create", name="创建实例数据", resource_type="asset", action="create"),
            Permission(code="asset:read", name="查看实例数据", resource_type="asset", action="read"),
            Permission(code="asset:update", name="维护实例数据", resource_type="asset", action="update"),
            Permission(code="asset:import", name="导入实例数据", resource_type="asset", action="import"),
            Permission(code="asset:export", name="导出实例数据", resource_type="asset", action="export"),
            Permission(code="agent:analyze", name="使用 AI 分析", resource_type="agent", action="analyze"),
        ]
        self.roles = [
            Role(code="admin", name="管理员", data_scope="all", permissions=[p.code for p in self.permissions]),
            Role(code="data_maintainer", name="数据维护员", data_scope="department", permissions=["dashboard:read", "metadata:read", "asset:create", "asset:read", "asset:update", "asset:import"]),
            Role(code="analyst", name="分析师", data_scope="department", permissions=["dashboard:read", "asset:read", "asset:export", "agent:analyze"]),
            Role(code="viewer", name="只读用户", data_scope="self", permissions=["dashboard:read", "asset:read"]),
        ]

    def create_attribute(self, payload: AttributeCreate) -> Attribute:
        self._require_entity_type(payload.entity_type_id)
        if any(item.entity_type_id == payload.entity_type_id and item.code == payload.code for item in self.attributes):
            raise ValueError(f"attribute code already exists for entity_type_id={payload.entity_type_id}: {payload.code}")
        attribute = Attribute(id=self._next_id(self.attributes), **payload.model_dump())
        self.attributes.append(attribute)
        return attribute

    def update_attribute(self, attribute_id: int, payload: AttributeUpdate) -> Attribute:
        attribute = self._require_attribute(attribute_id)
        data = attribute.model_dump()
        for key, value in payload.model_dump(exclude_unset=True).items():
            data[key] = value
        updated = Attribute(**data)
        self.attributes = [updated if item.id == attribute_id else item for item in self.attributes]
        return updated

    def create_entity_type(self, payload: EntityTypeCreate) -> EntityType:
        if any(item.code == payload.code for item in self.entity_types):
            raise ValueError(f"entity type code already exists: {payload.code}")
        if payload.category == "component" and payload.default_system_id is not None:
            self._require_system(payload.default_system_id)
        entity_type = EntityType(id=self._next_id(self.entity_types), **payload.model_dump())
        self.entity_types.append(entity_type)
        if entity_type.category == "component":
            self.attribute_groups.append(
                AttributeGroup(
                    id=self._next_id(self.attribute_groups),
                    entity_type_id=entity_type.id,
                    code="basic",
                    name="零部件信息",
                    sort_order=10,
                )
            )
        return entity_type

    def update_entity_type(self, entity_type_id: int, payload: EntityTypeUpdate) -> EntityType:
        entity_type = self._require_entity_type(entity_type_id)
        if payload.default_system_id is not None:
            self._require_system(payload.default_system_id)
        data = entity_type.model_dump()
        for key, value in payload.model_dump(exclude_unset=True).items():
            data[key] = value
        updated = EntityType(**data)
        self.entity_types = [updated if item.id == entity_type_id else item for item in self.entity_types]
        return updated

    def create_vehicle(self, payload: VehicleCreate) -> VehicleInstance:
        if any(item.vehicle_code == payload.vehicle_code for item in self.vehicles):
            raise ValueError(f"vehicle code already exists: {payload.vehicle_code}")
        vehicle_type = next(item for item in self.entity_types if item.category == "vehicle")
        vehicle = VehicleInstance(
            id=self._next_id(self.vehicles),
            entity_type_id=vehicle_type.id,
            vehicle_code=payload.vehicle_code,
            vehicle_name=payload.vehicle_name,
            source_type=payload.source_type,
            values=payload.values,
        )
        self.vehicles.append(vehicle)
        for system in self.systems:
            self.system_profiles.append(
                VehicleSystemProfile(
                    id=self._next_id(self.system_profiles),
                    vehicle_instance_id=vehicle.id,
                    system_id=system.id,
                    profile_name=f"{vehicle.vehicle_name} / {system.name}",
                )
            )
        return vehicle

    def create_component(self, payload: ComponentCreate) -> ComponentInstance:
        self._require_vehicle(payload.vehicle_instance_id)
        system = self._require_system(payload.system_id)
        entity_type = self._require_entity_type(payload.entity_type_id)
        if entity_type.category != "component":
            raise ValueError("component instance must use a component entity type")
        if entity_type.default_system_id and entity_type.default_system_id != system.id:
            raise ValueError("component entity type default system does not match selected system")
        if any(item.vehicle_instance_id == payload.vehicle_instance_id and item.component_code == payload.component_code for item in self.components):
            raise ValueError(f"component code already exists under vehicle_instance_id={payload.vehicle_instance_id}: {payload.component_code}")
        component = ComponentInstance(id=self._next_id(self.components), **payload.model_dump())
        self.components.append(component)
        return component

    def build_tree(self) -> list[AssetTreeNode]:
        vehicle_nodes: list[AssetTreeNode] = []
        for vehicle in self.vehicles:
            system_nodes: list[AssetTreeNode] = []
            for profile in sorted(
                [item for item in self.system_profiles if item.vehicle_instance_id == vehicle.id],
                key=lambda item: self._system(item.system_id).sort_order,
            ):
                system = self._system(profile.system_id)
                component_nodes = [
                    AssetTreeNode(
                        id=component.id,
                        node_type="component",
                        title=self._entity_type(component.entity_type_id).name,
                        code=component.component_code,
                        entity_type_id=component.entity_type_id,
                        system_id=component.system_id,
                        instance_id=component.id,
                    )
                    for component in self.components
                    if component.vehicle_instance_id == vehicle.id and component.system_id == system.id
                ]
                system_nodes.append(
                    AssetTreeNode(
                        id=profile.id,
                        node_type="system_profile",
                        title=system.name,
                        code=system.code,
                        system_id=system.id,
                        instance_id=profile.id,
                        children=component_nodes,
                    )
                )
            vehicle_nodes.append(
                AssetTreeNode(
                    id=vehicle.id,
                    node_type="vehicle",
                    title=vehicle.vehicle_name,
                    code=vehicle.vehicle_code,
                    entity_type_id=vehicle.entity_type_id,
                    instance_id=vehicle.id,
                    children=system_nodes,
                )
            )
        return vehicle_nodes

    def _system(self, system_id: int) -> SystemCatalog:
        return next(item for item in self.systems if item.id == system_id)

    def _entity_type(self, entity_type_id: int) -> EntityType:
        return next(item for item in self.entity_types if item.id == entity_type_id)

    def _require_system(self, system_id: int) -> SystemCatalog:
        for item in self.systems:
            if item.id == system_id:
                return item
        raise ValueError(f"system does not exist: {system_id}")

    def _require_entity_type(self, entity_type_id: int) -> EntityType:
        for item in self.entity_types:
            if item.id == entity_type_id:
                return item
        raise ValueError(f"entity type does not exist: {entity_type_id}")

    def _require_attribute(self, attribute_id: int) -> Attribute:
        for item in self.attributes:
            if item.id == attribute_id:
                return item
        raise ValueError(f"attribute does not exist: {attribute_id}")

    def _require_vehicle(self, vehicle_instance_id: int) -> VehicleInstance:
        for item in self.vehicles:
            if item.id == vehicle_instance_id:
                return item
        raise ValueError(f"vehicle instance does not exist: {vehicle_instance_id}")

    @staticmethod
    def _next_id(items: list) -> int:
        return max((item.id for item in items), default=0) + 1


store = CatalogStore()
