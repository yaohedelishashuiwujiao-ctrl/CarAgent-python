#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any


DEFAULT_SOURCE_DIR = Path("resources/autohome_corpus/current_sale")
OUTPUT_NAME = "mpv_configs_long.csv"
CONFIG_URL_TEMPLATE = "https://car.autohome.com.cn/config/series/{series_id}.html"

CSV_FIELDS = [
    "车系ID",
    "车系名称",
    "车型ID",
    "车型简称",
    "配置页URL",
    "字段ID",
    "分组",
    "字段名称",
    "业务分类",
    "配置值",
]


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _title_map(result: dict[str, Any]) -> dict[str, dict[str, str]]:
    title_by_id: dict[str, dict[str, str]] = {}
    for section in result.get("titlelist") or []:
        business_category = str(section.get("groupname") or "").strip()
        group = str(section.get("itemtype") or business_category or "其他").strip()
        for item in section.get("items") or []:
            title_id = str(item.get("titleid") or "").strip()
            field_name = str(item.get("itemname") or "").strip()
            if not title_id or not field_name:
                continue
            title_by_id[title_id] = {
                "分组": group,
                "字段名称": field_name,
                "业务分类": business_category,
            }
    return title_by_id


def _iter_rows(raw_path: Path) -> list[dict[str, str]]:
    payload = _load_json(raw_path)
    result = payload.get("result") or {}
    bread = result.get("bread") or {}
    series_id = str(bread.get("seriesid") or raw_path.stem).strip()
    series_name = str(bread.get("seriesname") or "").strip()
    config_url = CONFIG_URL_TEMPLATE.format(series_id=series_id)
    title_by_id = _title_map(result)

    rows: list[dict[str, str]] = []
    for spec in result.get("datalist") or []:
        spec_id = str(spec.get("specid") or "").strip()
        spec_name = str(spec.get("specname") or "").strip()
        if not spec_id:
            continue
        for item in spec.get("paramconflist") or []:
            title_id = str(item.get("titleid") or "").strip()
            if not title_id:
                continue
            title = title_by_id.get(title_id, {})
            value = item.get("itemname")
            if value is None:
                value = ""
            rows.append(
                {
                    "车系ID": series_id,
                    "车系名称": series_name,
                    "车型ID": spec_id,
                    "车型简称": spec_name,
                    "配置页URL": config_url,
                    "字段ID": title_id,
                    "分组": title.get("分组", "其他"),
                    "字段名称": title.get("字段名称", f"未知字段_{title_id}"),
                    "业务分类": title.get("业务分类", ""),
                    "配置值": str(value).strip(),
                }
            )
    return rows


def build_long_csv(source_dir: Path, output_path: Path) -> dict[str, int]:
    raw_dir = source_dir / "raw"
    if not raw_dir.exists():
        raise FileNotFoundError(f"raw directory not found: {raw_dir}")

    raw_files = sorted(raw_dir.glob("*.json"), key=lambda p: int(p.stem) if p.stem.isdigit() else p.stem)
    series_count = 0
    spec_ids: set[str] = set()
    row_count = 0

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for raw_path in raw_files:
            rows = _iter_rows(raw_path)
            if not rows:
                continue
            series_count += 1
            for row in rows:
                spec_ids.add(row["车型ID"])
                writer.writerow(row)
            row_count += len(rows)

    return {
        "series_count": series_count,
        "spec_count": len(spec_ids),
        "row_count": row_count,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Autohome long CSV from collected raw JSON corpus.")
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE_DIR)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    source_dir = args.source_dir
    output_path = args.output or (source_dir / OUTPUT_NAME)
    stats = build_long_csv(source_dir, output_path)
    print(f"wrote={output_path}")
    print(f"series_count={stats['series_count']}")
    print(f"spec_count={stats['spec_count']}")
    print(f"row_count={stats['row_count']}")


if __name__ == "__main__":
    main()
