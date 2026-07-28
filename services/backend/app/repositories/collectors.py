from __future__ import annotations

import json
from typing import Protocol

from backend.app.db import mysql_connection
from backend.app.schemas_collectors import CollectorTask, CollectorTaskCreate


DEFAULT_FIELDS = ["车型基本参数", "车身尺寸", "底盘/转向/制动参数", "图片链接", "页面来源 URL"]
DEFAULT_NOTES = [
    "采集结果先进入 evidence，不直接写入正式属性。",
    "正式使用前需要处理来源授权、频率限制和页面结构变化。",
    "字段入库前必须经过人工复核。",
]


class CollectorRepository(Protocol):
    def list_tasks(self) -> list[CollectorTask]: ...
    def create_task(self, payload: CollectorTaskCreate) -> CollectorTask: ...


class MemoryCollectorRepository:
    def __init__(self) -> None:
        self.tasks: list[CollectorTask] = []

    def list_tasks(self) -> list[CollectorTask]:
        return self.tasks

    def create_task(self, payload: CollectorTaskCreate) -> CollectorTask:
        task = CollectorTask(
            id=len(self.tasks) + 1,
            source=payload.source,
            target=payload.target,
            status="planned",
            strategy=payload.strategy,
            fields=DEFAULT_FIELDS,
            notes=DEFAULT_NOTES,
        )
        self.tasks.insert(0, task)
        return task


class MySqlCollectorRepository:
    def list_tasks(self) -> list[CollectorTask]:
        with mysql_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, source, target, status, strategy, fields_json, notes_json
                    FROM collector_task
                    ORDER BY id DESC
                    """
                )
                rows = cursor.fetchall()
        return [self._task_from_row(row) for row in rows]

    def create_task(self, payload: CollectorTaskCreate) -> CollectorTask:
        with mysql_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO collector_task (source, target, status, strategy, fields_json, notes_json)
                    VALUES (%s, %s, 'planned', %s, CAST(%s AS JSON), CAST(%s AS JSON))
                    """,
                    (
                        payload.source,
                        payload.target,
                        payload.strategy,
                        json.dumps(DEFAULT_FIELDS, ensure_ascii=False),
                        json.dumps(DEFAULT_NOTES, ensure_ascii=False),
                    ),
                )
                task_id = cursor.lastrowid
        return next(item for item in self.list_tasks() if item.id == task_id)

    def _task_from_row(self, row: dict) -> CollectorTask:
        fields = row["fields_json"] or []
        notes = row["notes_json"] or []
        if isinstance(fields, str):
            fields = json.loads(fields)
        if isinstance(notes, str):
            notes = json.loads(notes)
        return CollectorTask(
            id=row["id"],
            source=row["source"],
            target=row["target"],
            status=row["status"],
            strategy=row["strategy"],
            fields=fields,
            notes=notes,
        )
