from __future__ import annotations

import csv
import hashlib
import re
from collections import OrderedDict, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from backend.app.config import settings
from backend.app.db import DatabaseUnavailable, mysql_connection
from backend.app.schemas_autohome import (
    AutohomeFieldProfile,
    AutohomeImportRequest,
    AutohomeImportResponse,
    AutohomeScanRequest,
    AutohomeScanResponse,
)


DEFAULT_AUTOHOME_SOURCE_DIR = Path("/home/zhaoyunpeng/Projects/汽车之家/output_audited")
FIXED_ATTRIBUTES = [
    ("ah_brand_id", "汽车之家品牌ID", "integer", None, "基本参数", 1),
    ("ah_brand_name", "品牌", "text", None, "基本参数", 2),
    ("ah_series_id", "汽车之家车系ID", "integer", None, "基本参数", 3),
    ("ah_series_name", "车系名称", "text", None, "基本参数", 4),
    ("ah_spec_id", "汽车之家车型ID", "integer", None, "基本参数", 5),
    ("ah_spec_name", "车型版本名称", "text", None, "基本参数", 6),
    ("ah_config_url", "汽车之家配置页URL", "text", None, "基本参数", 7),
]
UNIT_RE = re.compile(r"[\(（]([^()（）]+)[\)）]")


@dataclass
class FieldAccumulator:
    title_id: str
    group: str
    field_name: str
    business_category: str = ""
    samples: list[str] = field(default_factory=list)
    non_empty_count: int = 0
    numeric_count: int = 0

    @property
    def code(self) -> str:
        digest = hashlib.sha1(f"{self.group}::{self.field_name}".encode("utf-8")).hexdigest()[:12]
        return f"ah_field_{digest}"

    @property
    def unit(self) -> str | None:
        match = UNIT_RE.search(self.field_name)
        if not match:
            return None
        unit = match.group(1).strip()
        return unit[:32] if unit else None

    @property
    def attr_type(self) -> str:
        if self.non_empty_count and self.numeric_count / self.non_empty_count >= 0.85:
            return "number"
        return "text"


class AutohomeDataService:
    def scan(self, payload: AutohomeScanRequest) -> AutohomeScanResponse:
        rows = self._read_long_rows(self._source_dir(payload.source_dir), payload.max_rows)
        fields = self._field_profiles(rows)
        series_ids = {row["车系ID"] for row in rows if row.get("车系ID")}
        spec_ids = {row["车型ID"] for row in rows if row.get("车型ID")}
        source_dir = self._source_dir(payload.source_dir)
        return AutohomeScanResponse(
            source_dir=str(source_dir),
            long_csv_path=str(source_dir / "mpv_configs_long.csv"),
            status="ready" if rows else "empty",
            series_count=len(series_ids),
            spec_count=len(spec_ids),
            field_count=len(fields),
            row_count=len(rows),
            groups=list(OrderedDict.fromkeys(field.group for field in fields if field.group)),
            fields=fields,
            notes=[
                "元数据按汽车之家字段分组+字段名生成稳定属性编码，例如 ah_field_xxx=车身::轴距(mm)。",
                "车型版本实例按 specid 入库，例如 AUTOHOME_SPEC_75600，不会把同一车系的不同版本混在一起。",
                "导入前会先发现字段，再创建缺失属性，最后写入车型版本实例属性值。",
            ],
        )

    def import_dataset(self, payload: AutohomeImportRequest) -> AutohomeImportResponse:
        source_dir = self._source_dir(payload.source_dir)
        rows = self._read_long_rows(source_dir, None)
        rows = self._limit_specs(rows, payload.max_specs)
        field_profiles = self._field_profiles(rows)
        specs = self._specs(rows)

        if payload.dry_run:
            return AutohomeImportResponse(
                source_dir=str(source_dir),
                status="dry_run",
                dry_run=True,
                series_count=len({item["series_id"] for item in specs.values()}),
                spec_count=len(specs),
                field_count=len(field_profiles),
                vehicle_created=0,
                vehicle_updated=0,
                attribute_created=0,
                attribute_reused=0,
                value_inserted=0,
                skipped_values=0,
                notes=["dry_run 只完成清洗和字段发现，没有写入数据库。"],
            )

        if settings.data_backend.lower() != "mysql":
            raise DatabaseUnavailable("汽车之家批量导入需要 DATA_BACKEND=mysql，避免大量实例数据只落到内存")

        with mysql_connection() as connection:
            with connection.cursor() as cursor:
                vehicle_type_id = self._ensure_vehicle_entity_type(cursor)
                group_ids = self._ensure_groups(cursor, vehicle_type_id, field_profiles)
                attribute_ids, created_attrs, reused_attrs = self._ensure_attributes(
                    cursor, vehicle_type_id, group_ids, field_profiles
                )
                vehicle_ids, created_vehicles, updated_vehicles = self._upsert_vehicles(cursor, vehicle_type_id, specs)
                inserted_values, skipped_values = self._replace_values(cursor, vehicle_ids, attribute_ids, rows, specs)

        return AutohomeImportResponse(
            source_dir=str(source_dir),
            status="imported",
            dry_run=False,
            series_count=len({item["series_id"] for item in specs.values()}),
            spec_count=len(specs),
            field_count=len(field_profiles),
            vehicle_created=created_vehicles,
            vehicle_updated=updated_vehicles,
            attribute_created=created_attrs,
            attribute_reused=reused_attrs,
            value_inserted=inserted_values,
            skipped_values=skipped_values,
            notes=[
                "已按车型版本 specid 创建整车实例，vehicle_code=AUTOHOME_SPEC_{specid}。",
                "已将汽车之家配置字段按分组+字段名写入整车动态属性元数据。",
                "重复导入会更新车型实例并替换同一批 ah_* 属性值，避免重复堆积。",
            ],
        )

    def _source_dir(self, source_dir: str | None) -> Path:
        return Path(source_dir).expanduser() if source_dir else DEFAULT_AUTOHOME_SOURCE_DIR

    def _read_long_rows(self, source_dir: Path, max_rows: int | None) -> list[dict[str, str]]:
        path = source_dir / "mpv_configs_long.csv"
        if not path.exists():
            raise FileNotFoundError(f"汽车之家长表不存在: {path}")
        rows: list[dict[str, str]] = []
        with path.open(encoding="utf-8-sig", newline="") as file:
            reader = csv.DictReader(file)
            for index, row in enumerate(reader, 1):
                rows.append({key: (value or "").strip() for key, value in row.items()})
                if max_rows is not None and index >= max_rows:
                    break
        return rows

    def _field_profiles(self, rows: list[dict[str, str]]) -> list[AutohomeFieldProfile]:
        accumulators: OrderedDict[str, FieldAccumulator] = OrderedDict()
        for row in rows:
            title_id = row.get("字段ID", "").strip()
            if not title_id:
                continue
            field_item = accumulators.setdefault(
                title_id,
                FieldAccumulator(
                    title_id=title_id,
                    group=row.get("分组", "").strip() or "其他",
                    field_name=row.get("字段名称", "").strip() or f"未知字段_{title_id}",
                    business_category=row.get("业务分类", "").strip(),
                ),
            )
            value = row.get("配置值", "").strip()
            if value and value != "-":
                field_item.non_empty_count += 1
                if self._number_or_none(value) is not None:
                    field_item.numeric_count += 1
                if value not in field_item.samples and len(field_item.samples) < 3:
                    field_item.samples.append(value)
        return [
            AutohomeFieldProfile(
                attribute_code=item.code,
                title_id=item.title_id,
                group=item.group,
                field_name=item.field_name,
                attr_type=item.attr_type,
                unit=item.unit,
                sample_values=item.samples,
                non_empty_count=item.non_empty_count,
            )
            for item in accumulators.values()
        ]

    def _specs(self, rows: list[dict[str, str]]) -> OrderedDict[str, dict[str, str]]:
        specs: OrderedDict[str, dict[str, str]] = OrderedDict()
        for row in rows:
            spec_id = row.get("车型ID", "").strip()
            if not spec_id:
                continue
            specs.setdefault(
                spec_id,
                {
                    "series_id": row.get("车系ID", "").strip(),
                    "series_name": row.get("车系名称", "").strip(),
                    "spec_id": spec_id,
                    "spec_name": row.get("车型简称", "").strip(),
                    "config_url": row.get("配置页URL", "").strip(),
                    "brand_id": "",
                    "brand_name": "",
                },
            )
            if row.get("字段ID") == "4" and row.get("配置值"):
                specs[spec_id]["brand_name"] = row["配置值"].strip()
        return specs

    def _limit_specs(self, rows: list[dict[str, str]], max_specs: int | None) -> list[dict[str, str]]:
        if not max_specs:
            return rows
        allowed: OrderedDict[str, None] = OrderedDict()
        for row in rows:
            spec_id = row.get("车型ID", "")
            if spec_id and spec_id not in allowed:
                allowed[spec_id] = None
                if len(allowed) >= max_specs:
                    break
        return [row for row in rows if row.get("车型ID", "") in allowed]

    def _ensure_vehicle_entity_type(self, cursor) -> int:
        cursor.execute("SELECT id FROM entity_type WHERE category='vehicle' AND code='vehicle' AND status <> 'deleted'")
        row = cursor.fetchone()
        if row:
            return row["id"]
        cursor.execute(
            """
            INSERT INTO entity_type (category, code, name, description, is_builtin, sort_order)
            VALUES ('vehicle', 'vehicle', '整车', '整车实体类型', TRUE, 10)
            """
        )
        return cursor.lastrowid

    def _ensure_groups(self, cursor, vehicle_type_id: int, fields: list[AutohomeFieldProfile]) -> dict[str, int]:
        desired: OrderedDict[str, tuple[str, int]] = OrderedDict()
        desired["基本参数"] = ("basic", 10)
        for index, field_item in enumerate(fields, 1):
            desired.setdefault(field_item.group or "其他", (f"ah_group_{self._group_code(field_item.group)}", 100 + index * 10))
        cursor.execute(
            """
            SELECT id, code, name FROM entity_attribute_group
            WHERE entity_type_id=%s AND status <> 'deleted'
            """,
            (vehicle_type_id,),
        )
        existing_rows = cursor.fetchall()
        by_name = {row["name"]: row["id"] for row in existing_rows}
        by_code = {row["code"]: row["id"] for row in existing_rows}
        group_ids: dict[str, int] = {}
        for name, (code, sort_order) in desired.items():
            if name in by_name:
                group_ids[name] = by_name[name]
                continue
            if code in by_code:
                group_ids[name] = by_code[code]
                continue
            cursor.execute(
                """
                INSERT INTO entity_attribute_group (entity_type_id, code, name, sort_order)
                VALUES (%s, %s, %s, %s)
                """,
                (vehicle_type_id, code[:64], name[:128], sort_order),
            )
            group_ids[name] = cursor.lastrowid
        return group_ids

    def _ensure_attributes(
        self,
        cursor,
        vehicle_type_id: int,
        group_ids: dict[str, int],
        fields: list[AutohomeFieldProfile],
    ) -> tuple[dict[str, dict[str, object]], int, int]:
        cursor.execute(
            """
            SELECT id, code FROM entity_attribute
            WHERE entity_type_id=%s AND status <> 'deleted'
            """,
            (vehicle_type_id,),
        )
        existing = {row["code"]: row["id"] for row in cursor.fetchall()}
        attribute_ids: dict[str, dict[str, object]] = {}
        created = 0
        reused = 0

        desired = [
            {
                "code": code,
                "name": name,
                "attr_type": attr_type,
                "unit": unit,
                "group": group,
                "sort_order": sort_order,
                "source_title_id": None,
            }
            for code, name, attr_type, unit, group, sort_order in FIXED_ATTRIBUTES
        ]
        desired.extend(
            {
                "code": field_item.attribute_code,
                "name": field_item.field_name,
                "attr_type": field_item.attr_type,
                "unit": field_item.unit,
                "group": field_item.group,
                "sort_order": 1000 + index,
                "source_title_id": field_item.title_id,
            }
            for index, field_item in enumerate(fields, 1)
        )

        for item in desired:
            code = str(item["code"])
            if code in existing:
                attribute_ids[code] = {"id": existing[code], "attr_type": item["attr_type"], "unit": item["unit"]}
                reused += 1
                continue
            config = '{"source":"autohome","source_title_id":%s}' % (
                "null" if item["source_title_id"] is None else f'"{item["source_title_id"]}"'
            )
            cursor.execute(
                """
                INSERT INTO entity_attribute
                (entity_type_id, group_id, code, name, attr_type, unit, is_required,
                 is_searchable, is_importable, is_exportable, is_multi_value, config_json, sort_order)
                VALUES (%s, %s, %s, %s, %s, %s, FALSE, TRUE, TRUE, TRUE, FALSE, CAST(%s AS JSON), %s)
                """,
                (
                    vehicle_type_id,
                    group_ids.get(str(item["group"])) or group_ids["基本参数"],
                    code,
                    str(item["name"])[:128],
                    item["attr_type"],
                    item["unit"],
                    config,
                    item["sort_order"],
                ),
            )
            attribute_ids[code] = {"id": cursor.lastrowid, "attr_type": item["attr_type"], "unit": item["unit"]}
            created += 1
        return attribute_ids, created, reused

    def _upsert_vehicles(self, cursor, vehicle_type_id: int, specs: OrderedDict[str, dict[str, str]]) -> tuple[dict[str, int], int, int]:
        vehicle_ids: dict[str, int] = {}
        created = 0
        updated = 0
        for spec_id, spec in specs.items():
            code = f"AUTOHOME_SPEC_{spec_id}"
            name_parts = [spec["series_name"], spec["spec_name"]]
            vehicle_name = " ".join(part for part in name_parts if part).strip() or code
            cursor.execute("SELECT id FROM vehicle_instance WHERE vehicle_code=%s", (code,))
            row = cursor.fetchone()
            if row:
                vehicle_ids[spec_id] = row["id"]
                cursor.execute(
                    """
                    UPDATE vehicle_instance
                    SET vehicle_name=%s, source_type='autohome', status='active'
                    WHERE id=%s
                    """,
                    (vehicle_name[:255], row["id"]),
                )
                updated += 1
            else:
                cursor.execute(
                    """
                    INSERT INTO vehicle_instance (entity_type_id, vehicle_code, vehicle_name, source_type)
                    VALUES (%s, %s, %s, 'autohome')
                    """,
                    (vehicle_type_id, code, vehicle_name[:255]),
                )
                vehicle_id = cursor.lastrowid
                vehicle_ids[spec_id] = vehicle_id
                cursor.execute("SELECT id, name, sort_order FROM system_catalog WHERE status='active' ORDER BY sort_order, id")
                for system in cursor.fetchall():
                    cursor.execute(
                        """
                        INSERT INTO vehicle_system_profile (vehicle_instance_id, system_id, profile_name, sort_order)
                        VALUES (%s, %s, %s, %s)
                        """,
                        (vehicle_id, system["id"], f"{vehicle_name} / {system['name']}"[:255], system["sort_order"]),
                    )
                created += 1
        return vehicle_ids, created, updated

    def _replace_values(
        self,
        cursor,
        vehicle_ids: dict[str, int],
        attribute_ids: dict[str, dict[str, object]],
        rows: list[dict[str, str]],
        specs: OrderedDict[str, dict[str, str]],
    ) -> tuple[int, int]:
        if not vehicle_ids:
            return 0, 0
        target_ids = list(vehicle_ids.values())
        placeholders = ", ".join(["%s"] * len(target_ids))
        cursor.execute(
            f"""
            DELETE v FROM instance_attribute_value v
            JOIN entity_attribute a ON a.id = v.attribute_id
            WHERE v.target_type='vehicle'
              AND v.target_id IN ({placeholders})
              AND a.code LIKE 'ah_%%'
            """,
            tuple(target_ids),
        )

        value_rows = []
        skipped = 0
        for spec_id, spec in specs.items():
            vehicle_id = vehicle_ids.get(spec_id)
            if not vehicle_id:
                continue
            fixed_values = {
                "ah_brand_id": spec.get("brand_id", ""),
                "ah_brand_name": spec.get("brand_name", ""),
                "ah_series_id": spec.get("series_id", ""),
                "ah_series_name": spec.get("series_name", ""),
                "ah_spec_id": spec.get("spec_id", ""),
                "ah_spec_name": spec.get("spec_name", ""),
                "ah_config_url": spec.get("config_url", ""),
            }
            for code, value in fixed_values.items():
                if value == "":
                    continue
                value_rows.append(self._value_tuple(vehicle_id, attribute_ids[code], value))

        for row in rows:
            spec_id = row.get("车型ID", "")
            value = row.get("配置值", "").strip()
            title_id = row.get("字段ID", "").strip()
            if not spec_id or not title_id or not value or value == "-":
                skipped += 1
                continue
            field_name = row.get("字段名称", "").strip() or f"未知字段_{title_id}"
            group = row.get("分组", "").strip() or "其他"
            digest = hashlib.sha1(f"{group}::{field_name}".encode("utf-8")).hexdigest()[:12]
            attr = attribute_ids.get(f"ah_field_{digest}")
            vehicle_id = vehicle_ids.get(spec_id)
            if not attr or not vehicle_id:
                skipped += 1
                continue
            value_rows.append(self._value_tuple(vehicle_id, attr, value))

        if value_rows:
            cursor.executemany(
                """
                INSERT INTO instance_attribute_value
                (target_type, target_id, attribute_id, value_text, value_number, unit, value_source, confidence)
                VALUES ('vehicle', %s, %s, %s, %s, %s, 'autohome', 0.9000)
                """,
                value_rows,
            )
        return len(value_rows), skipped

    def _value_tuple(self, vehicle_id: int, attr: dict[str, object], value: str):
        attr_type = attr["attr_type"]
        number = self._number_or_none(value) if attr_type == "number" else None
        text = None if number is not None else value
        return (vehicle_id, attr["id"], text, number, attr["unit"])

    def _group_code(self, name: str) -> str:
        if not name:
            return "other"
        normalized = re.sub(r"[^a-z0-9_]+", "_", name.lower()).strip("_")[:32]
        if normalized:
            return normalized
        return hashlib.sha1(name.encode("utf-8")).hexdigest()[:12]

    def _number_or_none(self, value: str) -> float | None:
        text = value.strip().replace(",", "")
        if not text or text in {"-", "暂无", "待查", "选装", "标配"}:
            return None
        if re.fullmatch(r"-?\d+(\.\d+)?", text):
            return float(text)
        return None


autohome_service = AutohomeDataService()
