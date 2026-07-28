from __future__ import annotations

import json
from typing import Protocol

from backend.app.db import mysql_connection
from backend.app.schemas import (
    Attribute,
    AttributeCreate,
    AttributeGroup,
    AttributeOption,
    AttributeUpdate,
    EntityType,
    EntityTypeCreate,
    EntityTypeUpdate,
    SystemCatalog,
)
from backend.app.services.catalog import store


class MetadataRepository(Protocol):
    def list_entity_types(self, category: str | None = None) -> list[EntityType]: ...
    def create_entity_type(self, payload: EntityTypeCreate) -> EntityType: ...
    def update_entity_type(self, entity_type_id: int, payload: EntityTypeUpdate) -> EntityType: ...
    def list_systems(self) -> list[SystemCatalog]: ...
    def list_attribute_groups(self, entity_type_id: int | None = None) -> list[AttributeGroup]: ...
    def list_attributes(self, entity_type_id: int | None = None) -> list[Attribute]: ...
    def create_attribute(self, payload: AttributeCreate) -> Attribute: ...
    def update_attribute(self, attribute_id: int, payload: AttributeUpdate) -> Attribute: ...


class MemoryMetadataRepository:
    def list_entity_types(self, category: str | None = None) -> list[EntityType]:
        if category:
            return [item for item in store.entity_types if item.category == category]
        return store.entity_types

    def create_entity_type(self, payload: EntityTypeCreate) -> EntityType:
        return store.create_entity_type(payload)

    def update_entity_type(self, entity_type_id: int, payload: EntityTypeUpdate) -> EntityType:
        return store.update_entity_type(entity_type_id, payload)

    def list_systems(self) -> list[SystemCatalog]:
        return store.systems

    def list_attribute_groups(self, entity_type_id: int | None = None) -> list[AttributeGroup]:
        if entity_type_id:
            return [item for item in store.attribute_groups if item.entity_type_id == entity_type_id]
        return store.attribute_groups

    def list_attributes(self, entity_type_id: int | None = None) -> list[Attribute]:
        if entity_type_id:
            return [item for item in store.attributes if item.entity_type_id == entity_type_id]
        return store.attributes

    def create_attribute(self, payload: AttributeCreate) -> Attribute:
        return store.create_attribute(payload)

    def update_attribute(self, attribute_id: int, payload: AttributeUpdate) -> Attribute:
        return store.update_attribute(attribute_id, payload)


class MySqlMetadataRepository:
    def list_entity_types(self, category: str | None = None) -> list[EntityType]:
        with mysql_connection() as connection:
            with connection.cursor() as cursor:
                sql = """
                    SELECT et.id, et.category, et.code, et.name, et.description, et.is_builtin,
                           et.status, et.sort_order, ces.system_id AS default_system_id
                    FROM entity_type et
                    LEFT JOIN component_entity_system ces
                      ON ces.entity_type_id = et.id AND ces.is_default = TRUE
                    WHERE et.status <> 'deleted'
                """
                params: tuple = ()
                if category:
                    sql += " AND et.category=%s"
                    params = (category,)
                sql += " ORDER BY et.sort_order, et.id"
                cursor.execute(sql, params)
                rows = cursor.fetchall()
        return [EntityType(**self._normalize_bool(row, "is_builtin")) for row in rows]

    def create_entity_type(self, payload: EntityTypeCreate) -> EntityType:
        with mysql_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT id FROM entity_type WHERE code=%s AND status <> 'deleted'", (payload.code,))
                if cursor.fetchone():
                    raise ValueError(f"entity type code already exists: {payload.code}")
                if payload.category == "component" and payload.default_system_id is not None:
                    self._require_system(cursor, payload.default_system_id)
                cursor.execute(
                    """
                    INSERT INTO entity_type (category, code, name, description, is_builtin, sort_order)
                    VALUES (%s, %s, %s, %s, FALSE, %s)
                    """,
                    (payload.category, payload.code, payload.name, payload.description, payload.sort_order),
                )
                entity_type_id = cursor.lastrowid
                if payload.category == "component":
                    if payload.default_system_id is not None:
                        cursor.execute(
                            """
                            INSERT INTO component_entity_system (entity_type_id, system_id, is_default)
                            VALUES (%s, %s, TRUE)
                            """,
                            (entity_type_id, payload.default_system_id),
                        )
                    cursor.execute(
                        """
                        INSERT INTO entity_attribute_group (entity_type_id, code, name, sort_order)
                        VALUES (%s, 'basic', '零部件信息', 10)
                        """,
                        (entity_type_id,),
                    )
        return self._require_entity_type(entity_type_id)

    def update_entity_type(self, entity_type_id: int, payload: EntityTypeUpdate) -> EntityType:
        with mysql_connection() as connection:
            with connection.cursor() as cursor:
                self._require_entity_type_row(cursor, entity_type_id)
                updates: list[str] = []
                params: list = []
                data = payload.model_dump(exclude_unset=True)
                for key in ["name", "description", "status"]:
                    if key in data:
                        updates.append(f"{key}=%s")
                        params.append(data[key])
                if updates:
                    params.append(entity_type_id)
                    cursor.execute(f"UPDATE entity_type SET {', '.join(updates)} WHERE id=%s", tuple(params))
                if "default_system_id" in data:
                    default_system_id = data["default_system_id"]
                    if default_system_id is not None:
                        self._require_system(cursor, default_system_id)
                    cursor.execute("DELETE FROM component_entity_system WHERE entity_type_id=%s", (entity_type_id,))
                    if default_system_id is not None:
                        cursor.execute(
                            """
                            INSERT INTO component_entity_system (entity_type_id, system_id, is_default)
                            VALUES (%s, %s, TRUE)
                            """,
                            (entity_type_id, default_system_id),
                        )
        return self._require_entity_type(entity_type_id)

    def list_systems(self) -> list[SystemCatalog]:
        with mysql_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, code, name, description, is_builtin, status, sort_order
                    FROM system_catalog
                    WHERE status <> 'deleted'
                    ORDER BY sort_order, id
                    """
                )
                rows = cursor.fetchall()
        return [SystemCatalog(**self._normalize_bool(row, "is_builtin")) for row in rows]

    def list_attribute_groups(self, entity_type_id: int | None = None) -> list[AttributeGroup]:
        with mysql_connection() as connection:
            with connection.cursor() as cursor:
                sql = """
                    SELECT id, entity_type_id, code, name, sort_order
                    FROM entity_attribute_group
                    WHERE status <> 'deleted'
                """
                params: tuple = ()
                if entity_type_id:
                    sql += " AND entity_type_id=%s"
                    params = (entity_type_id,)
                sql += " ORDER BY entity_type_id, sort_order, id"
                cursor.execute(sql, params)
                rows = cursor.fetchall()
        return [AttributeGroup(**row) for row in rows]

    def list_attributes(self, entity_type_id: int | None = None) -> list[Attribute]:
        with mysql_connection() as connection:
            with connection.cursor() as cursor:
                sql = """
                    SELECT id, entity_type_id, group_id, code, name, attr_type, unit,
                           is_required, is_searchable, is_importable, is_exportable,
                           is_multi_value, config_json, sort_order
                    FROM entity_attribute
                    WHERE status='active'
                """
                params: tuple = ()
                if entity_type_id:
                    sql += " AND entity_type_id=%s"
                    params = (entity_type_id,)
                sql += " ORDER BY entity_type_id, sort_order, id"
                cursor.execute(sql, params)
                rows = cursor.fetchall()
                options = self._load_options(cursor, [row["id"] for row in rows])
        return [self._attribute_from_row(row, options.get(row["id"], [])) for row in rows]

    def create_attribute(self, payload: AttributeCreate) -> Attribute:
        with mysql_connection() as connection:
            with connection.cursor() as cursor:
                self._require_entity_type_row(cursor, payload.entity_type_id)
                if payload.group_id is not None:
                    self._require_attribute_group(cursor, payload.group_id, payload.entity_type_id)
                cursor.execute(
                    """
                    SELECT id FROM entity_attribute
                    WHERE entity_type_id=%s AND code=%s AND status <> 'deleted'
                    """,
                    (payload.entity_type_id, payload.code),
                )
                if cursor.fetchone():
                    raise ValueError(f"attribute code already exists for entity_type_id={payload.entity_type_id}: {payload.code}")
                cursor.execute(
                    """
                    INSERT INTO entity_attribute
                    (entity_type_id, group_id, code, name, attr_type, unit, is_required,
                     is_searchable, is_importable, is_exportable, is_multi_value,
                     config_json, sort_order)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CAST(%s AS JSON), %s)
                    """,
                    (
                        payload.entity_type_id,
                        payload.group_id,
                        payload.code,
                        payload.name,
                        payload.attr_type,
                        payload.unit,
                        payload.is_required,
                        payload.is_searchable,
                        payload.is_importable,
                        payload.is_exportable,
                        payload.is_multi_value,
                        json.dumps(payload.config, ensure_ascii=False) if payload.config else None,
                        payload.sort_order,
                    ),
                )
                attribute_id = cursor.lastrowid
                self._replace_options(cursor, attribute_id, payload.options)
        return self._require_attribute(attribute_id)

    def update_attribute(self, attribute_id: int, payload: AttributeUpdate) -> Attribute:
        with mysql_connection() as connection:
            with connection.cursor() as cursor:
                self._require_attribute_row(cursor, attribute_id)
                updates: list[str] = []
                params: list = []
                data = payload.model_dump(exclude_unset=True)
                for key in [
                    "name",
                    "attr_type",
                    "unit",
                    "is_required",
                    "is_searchable",
                    "is_importable",
                    "is_exportable",
                    "is_multi_value",
                    "status",
                ]:
                    if key in data:
                        updates.append(f"{key}=%s")
                        params.append(data[key])
                if updates:
                    params.append(attribute_id)
                    cursor.execute(f"UPDATE entity_attribute SET {', '.join(updates)} WHERE id=%s", tuple(params))
        return self._require_attribute(attribute_id)

    def _require_entity_type(self, entity_type_id: int) -> EntityType:
        entity_types = [item for item in self.list_entity_types() if item.id == entity_type_id]
        if not entity_types:
            raise ValueError(f"entity type does not exist: {entity_type_id}")
        return entity_types[0]

    def _require_attribute(self, attribute_id: int) -> Attribute:
        attributes = [item for item in self.list_attributes() if item.id == attribute_id]
        if not attributes:
            raise ValueError(f"attribute does not exist: {attribute_id}")
        return attributes[0]

    def _require_system(self, cursor, system_id: int) -> None:
        cursor.execute("SELECT id FROM system_catalog WHERE id=%s AND status='active'", (system_id,))
        if not cursor.fetchone():
            raise ValueError(f"system does not exist: {system_id}")

    def _require_entity_type_row(self, cursor, entity_type_id: int) -> None:
        cursor.execute("SELECT id FROM entity_type WHERE id=%s AND status <> 'deleted'", (entity_type_id,))
        if not cursor.fetchone():
            raise ValueError(f"entity type does not exist: {entity_type_id}")

    def _require_attribute_group(self, cursor, group_id: int, entity_type_id: int) -> None:
        cursor.execute(
            """
            SELECT id FROM entity_attribute_group
            WHERE id=%s AND entity_type_id=%s AND status='active'
            """,
            (group_id, entity_type_id),
        )
        if not cursor.fetchone():
            raise ValueError(f"attribute group does not exist: {group_id}")

    def _require_attribute_row(self, cursor, attribute_id: int) -> None:
        cursor.execute("SELECT id FROM entity_attribute WHERE id=%s AND status <> 'deleted'", (attribute_id,))
        if not cursor.fetchone():
            raise ValueError(f"attribute does not exist: {attribute_id}")

    def _load_options(self, cursor, attribute_ids: list[int]) -> dict[int, list[AttributeOption]]:
        if not attribute_ids:
            return {}
        placeholders = ", ".join(["%s"] * len(attribute_ids))
        cursor.execute(
            f"""
            SELECT attribute_id, option_value, option_label, sort_order
            FROM entity_attribute_option
            WHERE status='active' AND attribute_id IN ({placeholders})
            ORDER BY attribute_id, sort_order, id
            """,
            tuple(attribute_ids),
        )
        grouped: dict[int, list[AttributeOption]] = {attribute_id: [] for attribute_id in attribute_ids}
        for row in cursor.fetchall():
            grouped.setdefault(row["attribute_id"], []).append(
                AttributeOption(value=row["option_value"], label=row["option_label"], sort_order=row["sort_order"])
            )
        return grouped

    def _replace_options(self, cursor, attribute_id: int, options: list[AttributeOption]) -> None:
        if not options:
            return
        for option in options:
            cursor.execute(
                """
                INSERT INTO entity_attribute_option (attribute_id, option_value, option_label, sort_order)
                VALUES (%s, %s, %s, %s)
                """,
                (attribute_id, option.value, option.label, option.sort_order),
            )

    def _attribute_from_row(self, row: dict, options: list[AttributeOption]) -> Attribute:
        data = dict(row)
        config = data.pop("config_json") or {}
        if isinstance(config, str):
            config = json.loads(config)
        for key in ["is_required", "is_searchable", "is_importable", "is_exportable", "is_multi_value"]:
            data[key] = bool(data[key])
        return Attribute(**data, config=config, options=options)

    def _normalize_bool(self, row: dict, key: str) -> dict:
        data = dict(row)
        data[key] = bool(data[key])
        return data
