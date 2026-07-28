from __future__ import annotations

from typing import Protocol

from backend.app.db import mysql_connection
from backend.app.schemas import Permission, Role
from backend.app.services.catalog import store


class PermissionRepository(Protocol):
    def list_roles(self) -> list[Role]: ...
    def list_permissions(self) -> list[Permission]: ...


class MemoryPermissionRepository:
    def list_roles(self) -> list[Role]:
        return store.roles

    def list_permissions(self) -> list[Permission]:
        return store.permissions


class MySqlPermissionRepository:
    def list_roles(self) -> list[Role]:
        permissions_by_role: dict[int, list[str]] = {}
        with mysql_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT id, code, name, data_scope FROM role WHERE status='active' ORDER BY id")
                roles = cursor.fetchall()
                cursor.execute(
                    """
                    SELECT rp.role_id, p.code
                    FROM role_permission rp
                    JOIN permission p ON p.id = rp.permission_id
                    ORDER BY rp.role_id, p.code
                    """
                )
                for row in cursor.fetchall():
                    permissions_by_role.setdefault(row["role_id"], []).append(row["code"])
        return [
            Role(
                code=row["code"],
                name=row["name"],
                data_scope=row["data_scope"],
                permissions=permissions_by_role.get(row["id"], []),
            )
            for row in roles
        ]

    def list_permissions(self) -> list[Permission]:
        with mysql_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT code, name, resource_type, action FROM permission ORDER BY resource_type, action, code")
                rows = cursor.fetchall()
        return [Permission(**row) for row in rows]
