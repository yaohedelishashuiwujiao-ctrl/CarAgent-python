from __future__ import annotations

from functools import lru_cache

from backend.app.config import settings
from backend.app.db import DatabaseUnavailable
from backend.app.repositories.metadata import MemoryMetadataRepository, MetadataRepository, MySqlMetadataRepository


@lru_cache(maxsize=1)
def get_metadata_repository() -> MetadataRepository:
    backend = settings.data_backend.lower()
    if backend == "memory":
        return MemoryMetadataRepository()
    if backend == "mysql":
        if not settings.database_url:
            raise DatabaseUnavailable("DATA_BACKEND=mysql requires DATABASE_URL")
        return MySqlMetadataRepository()
    raise DatabaseUnavailable(f"unsupported DATA_BACKEND: {settings.data_backend}")
