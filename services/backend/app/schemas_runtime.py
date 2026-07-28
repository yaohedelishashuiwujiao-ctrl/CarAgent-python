from __future__ import annotations

from pydantic import BaseModel, Field


class RuntimeCapability(BaseModel):
    key: str
    name: str
    status: str
    level: str
    detail: str


class RuntimeStatus(BaseModel):
    service: str
    environment: str
    capabilities: list[RuntimeCapability] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    vision_backend: dict[str, str] = Field(default_factory=dict)
