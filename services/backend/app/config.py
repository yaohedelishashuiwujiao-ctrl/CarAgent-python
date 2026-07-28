from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_env_file(Path(__file__).resolve().parents[2] / ".env")
_load_env_file(Path(__file__).resolve().parents[2] / ".env.local")


@dataclass(frozen=True)
class Settings:
    app_env: str = os.getenv("APP_ENV", "local")
    data_backend: str = os.getenv("DATA_BACKEND", "memory")
    database_url: str | None = os.getenv("DATABASE_URL")
    redis_url: str | None = os.getenv("REDIS_URL")
    agent_job_broker: str = os.getenv("AGENT_JOB_BROKER", "auto")
    runtime_token_secret: str | None = os.getenv("RUNTIME_TOKEN_SECRET")
    api_token_secret: str | None = os.getenv("API_TOKEN_SECRET")
    runtime_token_ttl_seconds: int = int(os.getenv("RUNTIME_TOKEN_TTL_SECONDS", "300"))
    token_issuer: str = os.getenv("TOKEN_ISSUER", "subjects-platform")
    api_token_audience: str = os.getenv("API_TOKEN_AUDIENCE", "subjects-platform-api")
    allow_insecure_dev_auth: bool = os.getenv("ALLOW_INSECURE_DEV_AUTH", "true").lower() in {"1", "true", "yes", "on"}
    default_agent_tools: tuple[str, ...] = tuple(
        item.strip()
        for item in os.getenv(
            "AGENT_DEFAULT_ALLOWED_TOOLS",
            "TodoWrite,ToolSearch,SubjectsAttributeLookup,SubjectsAttributeStats,SubjectsDataCatalogSearch,SubjectsSqlSchema,SubjectsSqlGlob,SubjectsSqlQuery,KnowledgeSearch,KnowledgeFetch,WebSearch,WebFetch,AutoChartGenerate,AutoPptxGenerate,StructuredOutput,SendUserMessage",
        ).split(",")
        if item.strip()
    )
    vision_detector_url: str | None = os.getenv("VISION_DETECTOR_URL")
    vision_segmentation_url: str | None = os.getenv("VISION_SEGMENTATION_URL")
    ark_api_key: str | None = os.getenv("ARK_API_KEY")
    ark_base_url: str | None = os.getenv("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")


settings = Settings()
