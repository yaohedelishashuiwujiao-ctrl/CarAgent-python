#!/usr/bin/env python3
"""Rate-limited collector for public Autohome on-sale configuration payloads.

It stores immutable raw snapshots first. Transformation into the platform's
long-table import format is deliberately a separate, auditable step.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import time
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


LIST_URL = "https://car.autohome.com.cn/price/list-0-{category}-0-0-0-0-0-0-0-0-0-0-0-0-0-{page}.html"
CONFIG_URL = "https://car-web-api.autohome.com.cn/car/param/getParamConf"
SERIES_RE = re.compile(r'<div class="list-cont" data-value="(\d+)"')
NEXT_RE = re.compile(r'class="page-item-next"[^>]+href="(?!javascript:void\(0\))')
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; SubjectsAgent-Platform/0.1)", "Accept-Language": "zh-CN,zh;q=0.9"}


def session(retries: int) -> requests.Session:
    client = requests.Session()
    policy = Retry(total=retries, connect=retries, read=retries, status=retries, backoff_factor=1, status_forcelist=(429, 500, 502, 503, 504), allowed_methods=("GET",))
    client.mount("https://", HTTPAdapter(max_retries=policy))
    client.headers.update(HEADERS)
    return client


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def discover(client: requests.Session, category: int, pages: int, timeout: float, delay: float) -> list[int]:
    found: list[int] = []
    seen: set[int] = set()
    for page in range(1, pages + 1):
        response = client.get(LIST_URL.format(category=category, page=page), timeout=timeout)
        response.raise_for_status()
        ids = [int(value) for value in SERIES_RE.findall(response.text)]
        if not ids:
            break
        for series_id in ids:
            if series_id not in seen:
                seen.add(series_id)
                found.append(series_id)
        if not NEXT_RE.search(response.text):
            break
        time.sleep(delay + random.uniform(0, delay * 0.5))
    return found


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect public Autohome on-sale configuration JSON with resumable snapshots.")
    parser.add_argument("--categories", default="1,2,3,4,5,6,7,8", help="Comma-separated public price-list category IDs.")
    parser.add_argument("--output", type=Path, default=Path("resources/autohome_corpus/current_sale"))
    parser.add_argument("--max-pages", type=int, default=50)
    parser.add_argument("--max-series", type=int, default=0, help="Safety cap; 0 means no cap.")
    parser.add_argument("--delay", type=float, default=1.5)
    parser.add_argument("--timeout", type=float, default=20)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if args.delay < 1:
        raise SystemExit("--delay must be at least one second")
    categories = [int(value) for value in args.categories.split(",") if value.strip()]
    client = session(args.retries)
    category_map = {str(category): discover(client, category, args.max_pages, args.timeout, args.delay) for category in categories}
    series_ids = list(dict.fromkeys(series_id for values in category_map.values() for series_id in values))
    if args.max_series:
        series_ids = series_ids[: args.max_series]
    raw_dir = args.output / "raw"
    failures: dict[str, str] = {}
    manifest: list[dict[str, object]] = []
    for position, series_id in enumerate(series_ids, 1):
        path = raw_dir / f"{series_id}.json"
        try:
            if path.exists() and not args.force:
                raw = path.read_bytes()
                payload = json.loads(raw)
                status = "cached"
            else:
                response = client.get(CONFIG_URL, params={"mode": 1, "site": 1, "seriesid": series_id}, timeout=args.timeout)
                response.raise_for_status()
                payload = response.json()
                if payload.get("returncode") != 0 or not payload.get("result"):
                    raise RuntimeError(str(payload.get("message") or "invalid config response"))
                raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
                status = "downloaded"
            bread = payload["result"].get("bread") or {}
            manifest.append({"series_id": series_id, "series_name": bread.get("seriesname"), "status": status, "sha256": hashlib.sha256(raw).hexdigest(), "artifact_path": str(path)})
        except Exception as exc:
            failures[str(series_id)] = str(exc)
            manifest.append({"series_id": series_id, "status": "error", "error": str(exc)})
        print(f"[{position}/{len(series_ids)}] {series_id} {manifest[-1]['status']}")
        time.sleep(args.delay + random.uniform(0, args.delay * 0.5))
    write_json(args.output / "discovered_series.json", category_map)
    write_json(args.output / "manifest.json", manifest)
    write_json(args.output / "failures.json", failures)
    print(f"series={len(series_ids)} ok={len(series_ids)-len(failures)} failed={len(failures)} output={args.output}")


if __name__ == "__main__":
    main()
