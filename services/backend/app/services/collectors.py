from __future__ import annotations

from functools import lru_cache

from backend.app.config import settings
from backend.app.db import DatabaseUnavailable
from backend.app.repositories.collectors import CollectorRepository, MemoryCollectorRepository, MySqlCollectorRepository


@lru_cache(maxsize=1)
def get_collector_repository() -> CollectorRepository:
    backend = settings.data_backend.lower()
    if backend == "memory":
        return MemoryCollectorRepository()
    if backend == "mysql":
        if not settings.database_url:
            raise DatabaseUnavailable("DATA_BACKEND=mysql requires DATABASE_URL")
        return MySqlCollectorRepository()
    raise DatabaseUnavailable(f"unsupported DATA_BACKEND: {settings.data_backend}")


collector_service = get_collector_repository()
