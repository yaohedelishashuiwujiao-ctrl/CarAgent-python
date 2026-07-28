from __future__ import annotations

from functools import lru_cache

from backend.app.config import settings
from backend.app.db import DatabaseUnavailable
from backend.app.repositories.permissions import MemoryPermissionRepository, MySqlPermissionRepository, PermissionRepository


@lru_cache(maxsize=1)
def get_permission_repository() -> PermissionRepository:
    backend = settings.data_backend.lower()
    if backend == "memory":
        return MemoryPermissionRepository()
    if backend == "mysql":
        if not settings.database_url:
            raise DatabaseUnavailable("DATA_BACKEND=mysql requires DATABASE_URL")
        return MySqlPermissionRepository()
    raise DatabaseUnavailable(f"unsupported DATA_BACKEND: {settings.data_backend}")
