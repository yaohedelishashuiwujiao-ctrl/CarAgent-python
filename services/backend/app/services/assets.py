from __future__ import annotations

from functools import lru_cache

from backend.app.config import settings
from backend.app.db import DatabaseUnavailable
from backend.app.repositories.assets import AssetRepository, MemoryAssetRepository, MySqlAssetRepository


@lru_cache(maxsize=1)
def get_asset_repository() -> AssetRepository:
    backend = settings.data_backend.lower()
    if backend == "memory":
        return MemoryAssetRepository()
    if backend == "mysql":
        if not settings.database_url:
            raise DatabaseUnavailable("DATA_BACKEND=mysql requires DATABASE_URL")
        return MySqlAssetRepository()
    raise DatabaseUnavailable(f"unsupported DATA_BACKEND: {settings.data_backend}")
