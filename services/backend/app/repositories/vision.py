from __future__ import annotations

import json
from typing import Protocol

from backend.app.db import mysql_connection
from backend.app.schemas_vision import VisionTask


class VisionRepository(Protocol):
    def list_tasks(self) -> list[VisionTask]: ...
    def create_task(self, file_name: str, detector_name: str, object_count: int, ai_summary: str, metadata: dict | None = None) -> VisionTask: ...


class MemoryVisionRepository:
    def __init__(self) -> None:
        self.tasks: list[VisionTask] = []

    def list_tasks(self) -> list[VisionTask]:
        return self.tasks

    def create_task(self, file_name: str, detector_name: str, object_count: int, ai_summary: str, metadata: dict | None = None) -> VisionTask:
        task = VisionTask(
            id=len(self.tasks) + 1,
            file_name=file_name,
            status="completed",
            detector_name=detector_name,
            object_count=object_count,
            ai_summary=ai_summary,
        )
        self.tasks.insert(0, task)
        return task


class MySqlVisionRepository:
    def list_tasks(self) -> list[VisionTask]:
        with mysql_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, file_name, status, detector_name, object_count, ai_summary
                    FROM vision_task
                    ORDER BY id DESC
                    """
                )
                rows = cursor.fetchall()
        return [VisionTask(**row) for row in rows]

    def create_task(self, file_name: str, detector_name: str, object_count: int, ai_summary: str, metadata: dict | None = None) -> VisionTask:
        with mysql_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO vision_task (file_name, status, detector_name, object_count, ai_summary, metadata_json)
                    VALUES (%s, 'completed', %s, %s, %s, CAST(%s AS JSON))
                    """,
                    (file_name, detector_name, object_count, ai_summary, json.dumps(metadata or {}, ensure_ascii=False)),
                )
                task_id = cursor.lastrowid
        return next(item for item in self.list_tasks() if item.id == task_id)
