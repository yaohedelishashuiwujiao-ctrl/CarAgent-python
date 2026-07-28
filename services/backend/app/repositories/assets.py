from __future__ import annotations

from typing import Protocol

from backend.app.cache import get_json, set_json
from backend.app.db import mysql_connection
from backend.app.schemas import (
    AssetTreeNode,
    ComponentCreate,
    ComponentInstance,
    InstanceValue,
    VehicleCreate,
    AttributeValueDetail,
    AttributeValueGroup,
    VehicleDetailResponse,
    VehicleInstance,
    VehicleListResponse,
    VehicleSeriesSummary,
    VehicleSystemProfile,
)
from backend.app.services.catalog import store


class AssetRepository(Protocol):
    def list_vehicles(self, include_values: bool = True) -> list[VehicleInstance]: ...
    def get_vehicle_detail(self, vehicle_id: int) -> VehicleDetailResponse: ...
    def list_vehicle_series(self, keyword: str | None = None, source_type: str | None = "autohome") -> list[VehicleSeriesSummary]: ...
    def search_vehicles(
        self,
        page: int = 1,
        page_size: int = 20,
        keyword: str | None = None,
        source_type: str | None = None,
        include_values: bool = False,
    ) -> VehicleListResponse: ...
    def create_vehicle(self, payload: VehicleCreate) -> VehicleInstance: ...
    def list_system_profiles(self, vehicle_instance_id: int | None = None) -> list[VehicleSystemProfile]: ...
    def list_components(self, vehicle_instance_id: int | None = None, system_id: int | None = None) -> list[ComponentInstance]: ...
    def create_component(self, payload: ComponentCreate) -> ComponentInstance: ...
    def build_tree(self) -> list[AssetTreeNode]: ...
    def build_lazy_tree(self, parent_type: str | None = None, parent_id: str | None = None, keyword: str | None = None) -> list[AssetTreeNode]: ...


class MemoryAssetRepository:
    def list_vehicles(self, include_values: bool = True) -> list[VehicleInstance]:
        if include_values:
            return store.vehicles
        return [VehicleInstance(**item.model_dump(exclude={"values"}), values=[]) for item in store.vehicles]

    def get_vehicle_detail(self, vehicle_id: int) -> VehicleDetailResponse:
        vehicle = next((item for item in store.vehicles if item.id == vehicle_id), None)
        if not vehicle:
            raise ValueError(f"vehicle instance does not exist: {vehicle_id}")
        return VehicleDetailResponse(
            vehicle=vehicle,
            groups=[
                AttributeValueGroup(
                    group_id=None,
                    group_code="basic",
                    group_name="属性值",
                    values=[
                        AttributeValueDetail(
                            attribute_id=item.attribute_id,
                            attribute_code=item.attribute_code,
                            attribute_name=item.attribute_code,
                            attr_type="text",
                            value=item.value,
                            unit=item.unit,
                            source=item.source,
                            confidence=item.confidence,
                        )
                        for item in vehicle.values
                    ],
                )
            ],
        )

    def list_vehicle_series(self, keyword: str | None = None, source_type: str | None = "autohome") -> list[VehicleSeriesSummary]:
        grouped: dict[str, VehicleSeriesSummary] = {}
        for vehicle in store.vehicles:
            if source_type and vehicle.source_type != source_type:
                continue
            series_id = next((str(item.value) for item in vehicle.values if item.attribute_code == "ah_series_id"), vehicle.vehicle_code)
            series_name = next((str(item.value) for item in vehicle.values if item.attribute_code == "ah_series_name"), vehicle.vehicle_name)
            if keyword and keyword not in f"{series_id} {series_name}":
                continue
            current = grouped.get(series_id)
            grouped[series_id] = VehicleSeriesSummary(
                series_id=series_id,
                series_name=series_name,
                source_type=vehicle.source_type,
                spec_count=(current.spec_count if current else 0) + 1,
            )
        return sorted(grouped.values(), key=lambda item: item.series_name)

    def search_vehicles(
        self,
        page: int = 1,
        page_size: int = 20,
        keyword: str | None = None,
        source_type: str | None = None,
        include_values: bool = False,
    ) -> VehicleListResponse:
        vehicles = self.list_vehicles(include_values=include_values)
        if source_type:
            vehicles = [item for item in vehicles if item.source_type == source_type]
        if keyword:
            text = keyword.lower()
            vehicles = [item for item in vehicles if text in f"{item.vehicle_code} {item.vehicle_name}".lower()]
        source_counts: dict[str, int] = {}
        for item in self.list_vehicles(include_values=False):
            source_counts[item.source_type] = source_counts.get(item.source_type, 0) + 1
        total = len(vehicles)
        offset = max(page - 1, 0) * page_size
        return VehicleListResponse(items=vehicles[offset : offset + page_size], total=total, page=page, page_size=page_size, source_counts=source_counts)

    def create_vehicle(self, payload: VehicleCreate) -> VehicleInstance:
        return store.create_vehicle(payload)

    def list_system_profiles(self, vehicle_instance_id: int | None = None) -> list[VehicleSystemProfile]:
        if vehicle_instance_id:
            return [item for item in store.system_profiles if item.vehicle_instance_id == vehicle_instance_id]
        return store.system_profiles

    def list_components(self, vehicle_instance_id: int | None = None, system_id: int | None = None) -> list[ComponentInstance]:
        components = store.components
        if vehicle_instance_id:
            components = [item for item in components if item.vehicle_instance_id == vehicle_instance_id]
        if system_id:
            components = [item for item in components if item.system_id == system_id]
        return components

    def create_component(self, payload: ComponentCreate) -> ComponentInstance:
        return store.create_component(payload)

    def build_tree(self) -> list[AssetTreeNode]:
        return store.build_tree()

    def build_lazy_tree(self, parent_type: str | None = None, parent_id: str | None = None, keyword: str | None = None) -> list[AssetTreeNode]:
        if parent_type == "vehicle":
            vehicle_id = int(parent_id or 0)
            return [
                AssetTreeNode(
                    id=profile.id,
                    node_type="system_profile",
                    title=profile.profile_name.split(" / ")[-1],
                    code=str(profile.system_id),
                    system_id=profile.system_id,
                    instance_id=profile.id,
                )
                for profile in self.list_system_profiles(vehicle_id)
            ]
        return [
            AssetTreeNode(id=item.id, node_type="vehicle", title=item.vehicle_name, code=item.vehicle_code, entity_type_id=item.entity_type_id, instance_id=item.id)
            for item in self.search_vehicles(keyword=keyword, page_size=50).items
        ]


class MySqlAssetRepository:
    def list_vehicles(self, include_values: bool = True) -> list[VehicleInstance]:
        with mysql_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, entity_type_id, vehicle_code, vehicle_name, source_type, status
                    FROM vehicle_instance
                    WHERE status <> 'deleted'
                    ORDER BY id DESC
                    """
                )
                rows = cursor.fetchall()
                values = self._load_values(cursor, "vehicle", [row["id"] for row in rows]) if include_values else {}
        return [VehicleInstance(**row, values=values.get(row["id"], [])) for row in rows]

    def get_vehicle_detail(self, vehicle_id: int) -> VehicleDetailResponse:
        with mysql_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, entity_type_id, vehicle_code, vehicle_name, source_type, status
                    FROM vehicle_instance
                    WHERE id=%s AND status <> 'deleted'
                    """,
                    (vehicle_id,),
                )
                row = cursor.fetchone()
                if not row:
                    raise ValueError(f"vehicle instance does not exist: {vehicle_id}")
                vehicle = VehicleInstance(**row, values=[])
                groups = self._load_grouped_values(cursor, "vehicle", vehicle_id)
                flat_values = [
                    InstanceValue(
                        attribute_id=item.attribute_id,
                        attribute_code=item.attribute_code,
                        value=item.value,
                        unit=item.unit,
                        source=item.source,
                        confidence=item.confidence,
                    )
                    for group in groups
                    for item in group.values
                ]
                vehicle.values = flat_values
        return VehicleDetailResponse(vehicle=vehicle, groups=groups)

    def list_vehicle_series(self, keyword: str | None = None, source_type: str | None = "autohome") -> list[VehicleSeriesSummary]:
        with mysql_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, code FROM entity_attribute
                    WHERE code IN ('ah_series_id', 'ah_series_name') AND status='active'
                    """
                )
                attr_ids = {row["code"]: row["id"] for row in cursor.fetchall()}
                if not {"ah_series_id", "ah_series_name"} <= set(attr_ids):
                    return []
                params: list[object] = [attr_ids["ah_series_id"], attr_ids["ah_series_name"]]
                where = ["v.status <> 'deleted'"]
                if source_type:
                    where.append("v.source_type=%s")
                    params.append(source_type)
                if keyword:
                    where.append("(series_name.value_text LIKE %s OR series_id.value_text LIKE %s)")
                    params.extend([f"%{keyword}%", f"%{keyword}%"])
                cursor.execute(
                    f"""
                    SELECT
                      series_id.value_text AS series_id,
                      COALESCE(series_name.value_text, series_id.value_text) AS series_name,
                      v.source_type,
                      COUNT(*) AS spec_count
                    FROM vehicle_instance v
                    JOIN instance_attribute_value series_id
                      ON series_id.target_type='vehicle'
                     AND series_id.target_id=v.id
                     AND series_id.attribute_id=%s
                     AND series_id.status='active'
                    LEFT JOIN instance_attribute_value series_name
                      ON series_name.target_type='vehicle'
                     AND series_name.target_id=v.id
                     AND series_name.attribute_id=%s
                     AND series_name.status='active'
                    WHERE {" AND ".join(where)}
                    GROUP BY series_id.value_text, series_name.value_text, v.source_type
                    ORDER BY series_name.value_text
                    """,
                    tuple(params),
                )
                rows = cursor.fetchall()
        return [
            VehicleSeriesSummary(
                series_id=str(row["series_id"]),
                series_name=str(row["series_name"]),
                source_type=row["source_type"],
                spec_count=int(row["spec_count"]),
            )
            for row in rows
        ]

    def search_vehicles(
        self,
        page: int = 1,
        page_size: int = 20,
        keyword: str | None = None,
        source_type: str | None = None,
        include_values: bool = False,
    ) -> VehicleListResponse:
        page = max(page, 1)
        page_size = min(max(page_size, 1), 100)
        where = ["status <> 'deleted'"]
        params: list[object] = []
        if source_type:
            where.append("source_type=%s")
            params.append(source_type)
        if keyword:
            where.append("(vehicle_code LIKE %s OR vehicle_name LIKE %s)")
            like = f"%{keyword}%"
            params.extend([like, like])
        where_sql = " AND ".join(where)
        offset = (page - 1) * page_size
        with mysql_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(f"SELECT COUNT(*) AS total FROM vehicle_instance WHERE {where_sql}", tuple(params))
                total = int(cursor.fetchone()["total"])
                cursor.execute(
                    f"""
                    SELECT id, entity_type_id, vehicle_code, vehicle_name, source_type, status
                    FROM vehicle_instance
                    WHERE {where_sql}
                    ORDER BY id DESC
                    LIMIT %s OFFSET %s
                    """,
                    tuple([*params, page_size, offset]),
                )
                rows = cursor.fetchall()
                values = self._load_values(cursor, "vehicle", [row["id"] for row in rows]) if include_values else {}
                source_counts = self._source_counts(cursor)
        return VehicleListResponse(
            items=[VehicleInstance(**row, values=values.get(row["id"], [])) for row in rows],
            total=total,
            page=page,
            page_size=page_size,
            source_counts=source_counts,
        )

    def _source_counts(self, cursor) -> dict[str, int]:
        cache_key = "assets:vehicles:source_counts:v1"
        cached = get_json(cache_key)
        if isinstance(cached, dict):
            return {str(key): int(value) for key, value in cached.items()}
        cursor.execute(
            """
            SELECT source_type, COUNT(*) AS count
            FROM vehicle_instance
            WHERE status <> 'deleted'
            GROUP BY source_type
            """
        )
        counts = {row["source_type"]: int(row["count"]) for row in cursor.fetchall()}
        set_json(cache_key, counts, ttl_seconds=60)
        return counts

    def create_vehicle(self, payload: VehicleCreate) -> VehicleInstance:
        with mysql_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT id FROM entity_type WHERE category='vehicle' AND status='active' ORDER BY id LIMIT 1")
                vehicle_type = cursor.fetchone()
                if not vehicle_type:
                    raise ValueError("vehicle entity type is not configured")
                cursor.execute("SELECT id FROM vehicle_instance WHERE vehicle_code=%s", (payload.vehicle_code,))
                if cursor.fetchone():
                    raise ValueError(f"vehicle code already exists: {payload.vehicle_code}")
                cursor.execute(
                    """
                    INSERT INTO vehicle_instance (entity_type_id, vehicle_code, vehicle_name, source_type)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (vehicle_type["id"], payload.vehicle_code, payload.vehicle_name, payload.source_type),
                )
                vehicle_id = cursor.lastrowid
                cursor.execute("SELECT id, name, sort_order FROM system_catalog WHERE status='active' ORDER BY sort_order, id")
                for system in cursor.fetchall():
                    cursor.execute(
                        """
                        INSERT INTO vehicle_system_profile (vehicle_instance_id, system_id, profile_name, sort_order)
                        VALUES (%s, %s, %s, %s)
                        """,
                        (vehicle_id, system["id"], f"{payload.vehicle_name} / {system['name']}", system["sort_order"]),
                    )
                self._insert_values(cursor, "vehicle", vehicle_id, payload.values)
        return self._require_vehicle(vehicle_id)

    def list_system_profiles(self, vehicle_instance_id: int | None = None) -> list[VehicleSystemProfile]:
        with mysql_connection() as connection:
            with connection.cursor() as cursor:
                sql = """
                    SELECT id, vehicle_instance_id, system_id, profile_name
                    FROM vehicle_system_profile
                    WHERE status <> 'deleted'
                """
                params: tuple = ()
                if vehicle_instance_id:
                    sql += " AND vehicle_instance_id=%s"
                    params = (vehicle_instance_id,)
                sql += " ORDER BY vehicle_instance_id, sort_order, id"
                cursor.execute(sql, params)
                rows = cursor.fetchall()
                values = self._load_values(cursor, "system_profile", [row["id"] for row in rows])
        return [VehicleSystemProfile(**row, values=values.get(row["id"], [])) for row in rows]

    def list_components(self, vehicle_instance_id: int | None = None, system_id: int | None = None) -> list[ComponentInstance]:
        with mysql_connection() as connection:
            with connection.cursor() as cursor:
                sql = """
                    SELECT id, vehicle_instance_id, system_id, entity_type_id, component_code, component_name, source_type, status
                    FROM component_instance
                    WHERE status <> 'deleted'
                """
                params: list[int] = []
                if vehicle_instance_id:
                    sql += " AND vehicle_instance_id=%s"
                    params.append(vehicle_instance_id)
                if system_id:
                    sql += " AND system_id=%s"
                    params.append(system_id)
                sql += " ORDER BY id DESC"
                cursor.execute(sql, tuple(params))
                rows = cursor.fetchall()
                values = self._load_values(cursor, "component", [row["id"] for row in rows])
        return [ComponentInstance(**row, values=values.get(row["id"], [])) for row in rows]

    def create_component(self, payload: ComponentCreate) -> ComponentInstance:
        with mysql_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT id FROM vehicle_instance WHERE id=%s AND status='active'", (payload.vehicle_instance_id,))
                if not cursor.fetchone():
                    raise ValueError(f"vehicle instance does not exist: {payload.vehicle_instance_id}")
                cursor.execute("SELECT id FROM system_catalog WHERE id=%s AND status='active'", (payload.system_id,))
                if not cursor.fetchone():
                    raise ValueError(f"system does not exist: {payload.system_id}")
                cursor.execute("SELECT id, category FROM entity_type WHERE id=%s AND status='active'", (payload.entity_type_id,))
                entity_type = cursor.fetchone()
                if not entity_type or entity_type["category"] != "component":
                    raise ValueError("component instance must use a component entity type")
                cursor.execute(
                    """
                    SELECT id FROM component_entity_system
                    WHERE entity_type_id=%s AND system_id=%s
                    """,
                    (payload.entity_type_id, payload.system_id),
                )
                if not cursor.fetchone():
                    raise ValueError("component entity type default system does not match selected system")
                cursor.execute(
                    "SELECT id FROM component_instance WHERE vehicle_instance_id=%s AND component_code=%s",
                    (payload.vehicle_instance_id, payload.component_code),
                )
                if cursor.fetchone():
                    raise ValueError(
                        f"component code already exists under vehicle_instance_id={payload.vehicle_instance_id}: {payload.component_code}"
                    )
                cursor.execute(
                    """
                    INSERT INTO component_instance
                    (vehicle_instance_id, system_id, entity_type_id, component_code, component_name, source_type)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        payload.vehicle_instance_id,
                        payload.system_id,
                        payload.entity_type_id,
                        payload.component_code,
                        payload.component_name,
                        payload.source_type,
                    ),
                )
                component_id = cursor.lastrowid
                self._insert_values(cursor, "component", component_id, payload.values)
        return self._require_component(component_id)

    def build_tree(self) -> list[AssetTreeNode]:
        vehicles = self.list_vehicles(include_values=False)
        profiles = self.list_system_profiles()
        components = self.list_components()
        with mysql_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT id, code, name, sort_order FROM system_catalog WHERE status='active'")
                systems = {row["id"]: row for row in cursor.fetchall()}
                cursor.execute("SELECT id, code, name FROM entity_type WHERE status='active'")
                entity_types = {row["id"]: row for row in cursor.fetchall()}
        nodes: list[AssetTreeNode] = []
        for vehicle in vehicles:
            system_nodes: list[AssetTreeNode] = []
            scoped_profiles = [item for item in profiles if item.vehicle_instance_id == vehicle.id]
            scoped_profiles.sort(key=lambda item: systems.get(item.system_id, {}).get("sort_order", 0))
            for profile in scoped_profiles:
                system = systems.get(profile.system_id, {"code": str(profile.system_id), "name": profile.profile_name})
                component_nodes = [
                    AssetTreeNode(
                        id=component.id,
                        node_type="component",
                        title=entity_types.get(component.entity_type_id, {}).get("name", component.component_name),
                        code=component.component_code,
                        entity_type_id=component.entity_type_id,
                        system_id=component.system_id,
                        instance_id=component.id,
                    )
                    for component in components
                    if component.vehicle_instance_id == vehicle.id and component.system_id == profile.system_id
                ]
                system_nodes.append(
                    AssetTreeNode(
                        id=profile.id,
                        node_type="system_profile",
                        title=system["name"],
                        code=system["code"],
                        system_id=profile.system_id,
                        instance_id=profile.id,
                        children=component_nodes,
                    )
                )
            nodes.append(
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
        return nodes

    def build_lazy_tree(self, parent_type: str | None = None, parent_id: str | None = None, keyword: str | None = None) -> list[AssetTreeNode]:
        if parent_type == "vehicle_series":
            return self._lazy_series_children(parent_id or "", keyword)
        if parent_type == "vehicle":
            return self._lazy_vehicle_children(int(parent_id or 0))
        if parent_type == "system_profile":
            return self._lazy_system_children(int(parent_id or 0))
        series = self.list_vehicle_series(keyword=keyword)
        if series:
            return [
                AssetTreeNode(
                    id=int(item.series_id) if item.series_id.isdigit() else index,
                    node_type="vehicle_series",
                    title=f"{item.series_name}（{item.spec_count} 个版本）",
                    code=item.series_id,
                )
                for index, item in enumerate(series, 1)
            ]
        return [
            AssetTreeNode(
                id=item.id,
                node_type="vehicle",
                title=item.vehicle_name,
                code=item.vehicle_code,
                entity_type_id=item.entity_type_id,
                instance_id=item.id,
            )
            for item in self.search_vehicles(page=1, page_size=50, keyword=keyword).items
        ]

    def _lazy_series_children(self, series_id: str, keyword: str | None = None) -> list[AssetTreeNode]:
        attr_id = self._attribute_id_by_code("ah_series_id")
        with mysql_connection() as connection:
            with connection.cursor() as cursor:
                params: list[object] = [attr_id, series_id]
                where = [
                    "v.status <> 'deleted'",
                    "series_id.attribute_id=%s",
                    "series_id.value_text=%s",
                    "series_id.target_type='vehicle'",
                    "series_id.target_id=v.id",
                    "series_id.status='active'",
                ]
                if keyword:
                    where.append("(v.vehicle_code LIKE %s OR v.vehicle_name LIKE %s)")
                    params.extend([f"%{keyword}%", f"%{keyword}%"])
                cursor.execute(
                    f"""
                    SELECT v.id, v.entity_type_id, v.vehicle_code, v.vehicle_name, v.source_type, v.status
                    FROM vehicle_instance v
                    JOIN instance_attribute_value series_id ON series_id.target_id=v.id
                    WHERE {" AND ".join(where)}
                    ORDER BY v.vehicle_name
                    LIMIT 200
                    """,
                    tuple(params),
                )
                rows = cursor.fetchall()
        return [
            AssetTreeNode(id=row["id"], node_type="vehicle", title=row["vehicle_name"], code=row["vehicle_code"], entity_type_id=row["entity_type_id"], instance_id=row["id"])
            for row in rows
        ]

    def _lazy_vehicle_children(self, vehicle_id: int) -> list[AssetTreeNode]:
        profiles = self.list_system_profiles(vehicle_id)
        with mysql_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT id, code, name FROM system_catalog WHERE status='active'")
                systems = {row["id"]: row for row in cursor.fetchall()}
        return [
            AssetTreeNode(
                id=profile.id,
                node_type="system_profile",
                title=systems.get(profile.system_id, {}).get("name", profile.profile_name),
                code=systems.get(profile.system_id, {}).get("code", str(profile.system_id)),
                system_id=profile.system_id,
                instance_id=profile.id,
            )
            for profile in profiles
        ]

    def _lazy_system_children(self, profile_id: int) -> list[AssetTreeNode]:
        with mysql_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT vehicle_instance_id, system_id FROM vehicle_system_profile WHERE id=%s AND status <> 'deleted'",
                    (profile_id,),
                )
                profile = cursor.fetchone()
                if not profile:
                    return []
                cursor.execute(
                    """
                    SELECT c.id, c.system_id, c.entity_type_id, c.component_code, c.component_name, et.name AS entity_type_name
                    FROM component_instance c
                    JOIN entity_type et ON et.id=c.entity_type_id
                    WHERE c.vehicle_instance_id=%s AND c.system_id=%s AND c.status <> 'deleted'
                    ORDER BY c.id DESC
                    """,
                    (profile["vehicle_instance_id"], profile["system_id"]),
                )
                rows = cursor.fetchall()
        return [
            AssetTreeNode(id=row["id"], node_type="component", title=row["component_name"] or row["entity_type_name"], code=row["component_code"], entity_type_id=row["entity_type_id"], system_id=row["system_id"], instance_id=row["id"])
            for row in rows
        ]

    def _require_vehicle(self, vehicle_id: int) -> VehicleInstance:
        vehicles = [item for item in self.list_vehicles() if item.id == vehicle_id]
        if not vehicles:
            raise ValueError(f"vehicle instance does not exist: {vehicle_id}")
        return vehicles[0]

    def _require_component(self, component_id: int) -> ComponentInstance:
        components = [item for item in self.list_components() if item.id == component_id]
        if not components:
            raise ValueError(f"component instance does not exist: {component_id}")
        return components[0]

    def _attribute_id_by_code(self, code: str) -> int:
        with mysql_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT id FROM entity_attribute WHERE code=%s AND status='active' ORDER BY id LIMIT 1", (code,))
                row = cursor.fetchone()
        if not row:
            raise ValueError(f"attribute does not exist: {code}")
        return row["id"]

    def _load_grouped_values(self, cursor, target_type: str, target_id: int) -> list[AttributeValueGroup]:
        cursor.execute(
            """
            SELECT
              g.id AS group_id,
              COALESCE(g.code, 'ungrouped') AS group_code,
              COALESCE(g.name, '未分组') AS group_name,
              COALESCE(g.sort_order, 999999) AS group_sort_order,
              a.id AS attribute_id,
              a.code AS attribute_code,
              a.name AS attribute_name,
              a.attr_type,
              v.value_text,
              v.value_number,
              v.value_datetime,
              v.value_boolean,
              v.value_json,
              v.unit,
              v.value_source,
              v.confidence,
              a.sort_order AS attribute_sort_order
            FROM instance_attribute_value v
            JOIN entity_attribute a ON a.id=v.attribute_id
            LEFT JOIN entity_attribute_group g ON g.id=a.group_id
            WHERE v.target_type=%s AND v.target_id=%s AND v.status='active'
            ORDER BY group_sort_order, g.id, a.sort_order, a.id
            """,
            (target_type, target_id),
        )
        groups: dict[int | None, AttributeValueGroup] = {}
        for row in cursor.fetchall():
            group_id = row["group_id"]
            group = groups.setdefault(
                group_id,
                AttributeValueGroup(
                    group_id=group_id,
                    group_code=row["group_code"],
                    group_name=row["group_name"],
                    values=[],
                ),
            )
            group.values.append(
                AttributeValueDetail(
                    attribute_id=row["attribute_id"],
                    attribute_code=row["attribute_code"],
                    attribute_name=row["attribute_name"],
                    attr_type=row["attr_type"],
                    value=self._row_value(row),
                    unit=row["unit"],
                    source=row["value_source"],
                    confidence=float(row["confidence"]) if row["confidence"] is not None else None,
                )
            )
        return list(groups.values())

    def _load_values(self, cursor, target_type: str, target_ids: list[int]) -> dict[int, list[InstanceValue]]:
        if not target_ids:
            return {}
        placeholders = ", ".join(["%s"] * len(target_ids))
        cursor.execute(
            f"""
            SELECT v.target_id, v.attribute_id, a.code AS attribute_code, a.attr_type, v.value_text,
                   v.value_number, v.value_datetime, v.value_boolean, v.value_json, v.unit,
                   v.value_source, v.confidence
            FROM instance_attribute_value v
            JOIN entity_attribute a ON a.id = v.attribute_id
            WHERE v.target_type=%s AND v.target_id IN ({placeholders}) AND v.status='active'
            ORDER BY v.id
            """,
            tuple([target_type, *target_ids]),
        )
        grouped: dict[int, list[InstanceValue]] = {target_id: [] for target_id in target_ids}
        for row in cursor.fetchall():
            grouped.setdefault(row["target_id"], []).append(
                InstanceValue(
                    attribute_id=row["attribute_id"],
                    attribute_code=row["attribute_code"],
                    value=self._row_value(row),
                    unit=row["unit"],
                    source=row["value_source"],
                    confidence=float(row["confidence"]) if row["confidence"] is not None else None,
                )
            )
        return grouped

    def _insert_values(self, cursor, target_type: str, target_id: int, values: list[InstanceValue]) -> None:
        for item in values:
            value_text = value_number = value_boolean = value_json = None
            if isinstance(item.value, bool):
                value_boolean = item.value
            elif isinstance(item.value, (int, float)):
                value_number = item.value
            elif isinstance(item.value, (dict, list)):
                import json

                value_json = json.dumps(item.value, ensure_ascii=False)
            else:
                value_text = str(item.value)
            cursor.execute(
                """
                INSERT INTO instance_attribute_value
                (target_type, target_id, attribute_id, value_text, value_number, value_boolean, value_json, unit, value_source, confidence)
                VALUES (%s, %s, %s, %s, %s, %s, CAST(%s AS JSON), %s, %s, %s)
                """,
                (
                    target_type,
                    target_id,
                    item.attribute_id,
                    value_text,
                    value_number,
                    value_boolean,
                    value_json,
                    item.unit,
                    item.source,
                    item.confidence,
                ),
            )

    def _row_value(self, row: dict):
        if row["value_number"] is not None:
            number = float(row["value_number"])
            return int(number) if number.is_integer() else number
        if row["value_boolean"] is not None:
            return bool(row["value_boolean"])
        if row["value_json"] is not None:
            return row["value_json"]
        if row["value_datetime"] is not None:
            return row["value_datetime"].isoformat()
        return row["value_text"]
