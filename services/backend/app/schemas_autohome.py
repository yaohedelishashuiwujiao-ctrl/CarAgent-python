from __future__ import annotations

from pydantic import BaseModel, Field


class AutohomeFieldProfile(BaseModel):
    attribute_code: str
    title_id: str
    group: str
    field_name: str
    attr_type: str
    unit: str | None = None
    sample_values: list[str] = Field(default_factory=list)
    non_empty_count: int = 0


class AutohomeScanRequest(BaseModel):
    source_dir: str | None = None
    max_rows: int | None = None


class AutohomeScanResponse(BaseModel):
    source_dir: str
    long_csv_path: str
    status: str
    series_count: int
    spec_count: int
    field_count: int
    row_count: int
    groups: list[str]
    fields: list[AutohomeFieldProfile]
    notes: list[str]


class AutohomeImportRequest(BaseModel):
    source_dir: str | None = None
    max_specs: int | None = None
    dry_run: bool = False


class AutohomeImportResponse(BaseModel):
    source_dir: str
    status: str
    dry_run: bool
    series_count: int
    spec_count: int
    field_count: int
    vehicle_created: int
    vehicle_updated: int
    attribute_created: int
    attribute_reused: int
    value_inserted: int
    skipped_values: int
    notes: list[str]
