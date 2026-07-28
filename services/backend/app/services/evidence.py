from __future__ import annotations

from functools import lru_cache

from backend.app.config import settings
from backend.app.db import DatabaseUnavailable
from backend.app.repositories.evidence import EvidenceRepository, MemoryEvidenceRepository, MySqlEvidenceRepository


@lru_cache(maxsize=1)
def get_evidence_repository() -> EvidenceRepository:
    backend = settings.data_backend.lower()
    if backend == "memory":
        return MemoryEvidenceRepository()
    if backend == "mysql":
        if not settings.database_url:
            raise DatabaseUnavailable("DATA_BACKEND=mysql requires DATABASE_URL")
        return MySqlEvidenceRepository()
    raise DatabaseUnavailable(f"unsupported DATA_BACKEND: {settings.data_backend}")


evidence_service = get_evidence_repository()
