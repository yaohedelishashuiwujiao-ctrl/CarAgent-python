from __future__ import annotations

from pydantic import BaseModel


class CollectorTask(BaseModel):
    id: int
    source: str
    target: str
    status: str
    strategy: str
    fields: list[str]
    notes: list[str]


class CollectorTaskCreate(BaseModel):
    source: str = "autohome"
    target: str
    strategy: str = "vehicle_profile"
