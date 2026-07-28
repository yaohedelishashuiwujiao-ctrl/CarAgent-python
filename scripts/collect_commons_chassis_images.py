#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import re
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import requests
from requests import Response


API_URL = "https://commons.wikimedia.org/w/api.php"


@dataclass(frozen=True)
class ClassQuery:
    class_id: int
    code: str
    queries: list[str]
    categories: list[str]


CLASSES = [
    ClassQuery(0, "upper_control_arm", ["upper control arm", "wishbone suspension arm", "automotive control arm", "A-arm suspension"], ["Control arms", "Automobile suspension arms"]),
    ClassQuery(1, "lower_control_arm", ["lower control arm", "wishbone suspension arm", "automotive control arm", "suspension arm car"], ["Control arms", "Automobile suspension arms"]),
    ClassQuery(2, "front_subframe", ["automotive subframe", "front subframe car", "engine cradle", "vehicle subframe"], ["Subframes", "Automobile chassis"]),
    ClassQuery(3, "brake_disc", ["brake disc", "disc brake rotor", "automotive brake rotor", "disk brake car"], ["Disc brakes", "Brake discs"]),
    ClassQuery(4, "brake_caliper", ["brake caliper", "disc brake caliper", "automotive brake caliper", "car brake caliper"], ["Brake calipers", "Disc brakes"]),
    ClassQuery(5, "steering_knuckle", ["steering knuckle", "automotive upright", "wheel carrier car", "hub carrier suspension"], ["Steering knuckles", "Automobile suspension"]),
    ClassQuery(6, "tie_rod", ["tie rod end", "steering tie rod", "track rod car", "automotive tie rod"], ["Tie rods", "Steering linkage"]),
    ClassQuery(7, "drive_shaft", ["cv axle shaft", "half shaft axle", "drive shaft automobile", "constant velocity joint axle"], ["Drive shafts", "Constant-velocity joints"]),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect public chassis part images from Wikimedia Commons.")
    parser.add_argument("--output", default="resources/chassis_seed_dataset")
    parser.add_argument("--per-class", type=int, default=30)
    parser.add_argument("--sleep", type=float, default=0.2)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--create-yolo", action="store_true", default=True)
    parser.add_argument("--weak-box-size", type=float, default=0.9)
    parser.add_argument("--min-image-bytes", type=int, default=10_000)
    parser.add_argument("--download-retries", type=int, default=2)
    parser.add_argument("--api-retries", type=int, default=3)
    parser.add_argument("--retry-sleep", type=float, default=3.0)
    parser.add_argument("--class-sleep", type=float, default=2.0)
    parser.add_argument("--max-candidates", type=int, default=80)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    random.seed(args.seed)
    output = Path(args.output)
    raw_dir = output / "raw"
    yolo_dir = output / "yolo"
    manifest_path = output / "manifest.csv"
    output.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict] = []
    seen_sha256: set[str] = set()
    session = requests.Session()
    session.headers.update({"User-Agent": "ChassisBenchmarkDatasetBuilder/0.1 (research dataset bootstrap)"})

    for class_query in CLASSES:
        class_dir = raw_dir / class_query.code
        class_dir.mkdir(parents=True, exist_ok=True)
        candidates = []
        for query in class_query.queries:
            candidates.extend(search_commons(session, query, limit=args.per_class * 3, retries=args.api_retries, retry_sleep=args.retry_sleep))
            time.sleep(args.sleep)
        for category in class_query.categories:
            candidates.extend(category_members(session, category, limit=args.per_class * 3, retries=args.api_retries, retry_sleep=args.retry_sleep))
            time.sleep(args.sleep)
        candidates = list(dict.fromkeys(candidates))
        if args.max_candidates > 0:
            candidates = candidates[: args.max_candidates]
        collected = 0
        stats = {"candidate": len(candidates), "no_info": 0, "unsupported": 0, "download_error": 0, "too_small": 0, "duplicate": 0}
        for title in candidates:
            if collected >= args.per_class:
                break
            info = image_info(session, title, retries=args.api_retries, retry_sleep=args.retry_sleep)
            if not info:
                stats["no_info"] += 1
                continue
            if not is_supported_image(info.get("url", "")):
                stats["unsupported"] += 1
                continue
            image_url = info["url"]
            try:
                content = download(session, image_url, retries=args.download_retries, retry_sleep=args.retry_sleep)
            except requests.RequestException:
                stats["download_error"] += 1
                continue
            if len(content) < args.min_image_bytes:
                stats["too_small"] += 1
                continue
            sha256 = hashlib.sha256(content).hexdigest()
            if sha256 in seen_sha256:
                stats["duplicate"] += 1
                continue
            seen_sha256.add(sha256)
            suffix = suffix_from_url(image_url)
            file_name = f"{class_query.code}_{collected + 1:04d}_{sha256[:10]}{suffix}"
            local_path = class_dir / file_name
            local_path.write_bytes(content)
            row = {
                "class_id": class_query.class_id,
                "class_code": class_query.code,
                "file_name": file_name,
                "local_path": str(local_path),
                "source": "wikimedia_commons",
                "source_title": title,
                "source_url": image_url,
                "license": info.get("extmetadata", {}).get("LicenseShortName", {}).get("value", ""),
                "artist": strip_html(info.get("extmetadata", {}).get("Artist", {}).get("value", "")),
                "credit": strip_html(info.get("extmetadata", {}).get("Credit", {}).get("value", "")),
                "label_quality": "weak_auto_box",
            }
            rows.append(row)
            collected += 1
            time.sleep(args.sleep)
        print(f"{class_query.code}: collected {collected} / candidates {stats['candidate']} / skipped {stats}", flush=True)
        time.sleep(args.class_sleep)

    write_manifest(manifest_path, rows)
    if args.create_yolo:
        create_weak_yolo(output, rows, yolo_dir, args.weak_box_size, args.seed)
    print(f"manifest: {manifest_path}")
    print(f"images: {raw_dir}")
    print(f"yolo: {yolo_dir}")


def search_commons(session: requests.Session, query: str, limit: int, retries: int, retry_sleep: float) -> list[str]:
    params = {
        "action": "query",
        "format": "json",
        "generator": "search",
        "gsrsearch": query,
        "gsrnamespace": 6,
        "gsrlimit": min(limit, 50),
    }
    data = get_json(session, params, retries=retries, retry_sleep=retry_sleep)
    if not data:
        return []
    pages = data.get("query", {}).get("pages", {})
    titles = [page["title"] for page in pages.values() if page.get("title", "").lower().startswith("file:")]
    return titles


def category_members(session: requests.Session, category: str, limit: int, retries: int, retry_sleep: float) -> list[str]:
    params = {
        "action": "query",
        "format": "json",
        "list": "categorymembers",
        "cmtitle": f"Category:{category}",
        "cmnamespace": 6,
        "cmlimit": min(limit, 50),
    }
    data = get_json(session, params, retries=retries, retry_sleep=retry_sleep)
    if not data:
        return []
    return [item["title"] for item in data.get("query", {}).get("categorymembers", []) if item.get("title", "").lower().startswith("file:")]


def image_info(session: requests.Session, title: str, retries: int, retry_sleep: float) -> dict | None:
    params = {
        "action": "query",
        "format": "json",
        "titles": title,
        "prop": "imageinfo",
        "iiprop": "url|mime|extmetadata",
    }
    data = get_json(session, params, retries=retries, retry_sleep=retry_sleep)
    if not data:
        return None
    pages = data.get("query", {}).get("pages", {})
    for page in pages.values():
        infos = page.get("imageinfo", [])
        if infos:
            return infos[0]
    return None


def get_json(session: requests.Session, params: dict, retries: int, retry_sleep: float) -> dict | None:
    for attempt in range(retries + 1):
        try:
            response = session.get(API_URL, params=params, timeout=30)
            if response.status_code in {429, 503} and attempt < retries:
                time.sleep(retry_sleep * (attempt + 1))
                continue
            response.raise_for_status()
            return parse_json_response(response)
        except (requests.RequestException, ValueError):
            if attempt < retries:
                time.sleep(retry_sleep * (attempt + 1))
                continue
            return None
    return None


def parse_json_response(response: Response) -> dict:
    content_type = response.headers.get("content-type", "")
    if "json" not in content_type and not response.text.lstrip().startswith("{"):
        raise ValueError(f"unexpected content type: {content_type}")
    return response.json()


def download(session: requests.Session, url: str, retries: int, retry_sleep: float) -> bytes:
    last_error: requests.RequestException | None = None
    for attempt in range(retries + 1):
        response = session.get(url, timeout=60)
        if response.status_code == 429 and attempt < retries:
            retry_after = response.headers.get("retry-after")
            sleep_for = retry_sleep
            if retry_after and retry_after.isdigit():
                sleep_for = min(float(retry_after), retry_sleep * (attempt + 1))
            time.sleep(sleep_for)
            continue
        try:
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            if not content_type.startswith("image/"):
                raise requests.RequestException(f"not an image: {content_type}")
            return response.content
        except requests.RequestException as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(retry_sleep * (attempt + 1))
    raise last_error or requests.RequestException("download failed")


def is_supported_image(url: str) -> bool:
    return suffix_from_url(url).lower() in {".jpg", ".jpeg", ".png", ".webp"}


def suffix_from_url(url: str) -> str:
    path = urlparse(url).path
    suffix = Path(path).suffix.lower()
    return ".jpg" if suffix == ".jpeg" else suffix


def strip_html(value: str) -> str:
    return re.sub(r"<[^>]+>", " ", value or "").strip()


def write_manifest(path: Path, rows: list[dict]) -> None:
    fields = [
        "class_id",
        "class_code",
        "file_name",
        "local_path",
        "source",
        "source_title",
        "source_url",
        "license",
        "artist",
        "credit",
        "label_quality",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    with path.with_suffix(".jsonl").open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def create_weak_yolo(output: Path, rows: list[dict], yolo_dir: Path, weak_box_size: float, seed: int) -> None:
    import shutil

    rng = random.Random(seed)
    shuffled = rows[:]
    rng.shuffle(shuffled)
    for index, row in enumerate(shuffled):
        split = "train" if index % 10 < 8 else "val"
        image_src = Path(row["local_path"])
        image_dst = yolo_dir / "images" / split / image_src.name
        label_dst = yolo_dir / "labels" / split / f"{image_src.stem}.txt"
        image_dst.parent.mkdir(parents=True, exist_ok=True)
        label_dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(image_src, image_dst)
        box = weak_box_size
        label_dst.write_text(f"{row['class_id']} 0.5 0.5 {box:.6f} {box:.6f}\n", encoding="utf-8")
    data_yaml = yolo_dir / "chassis_parts_weak.yaml"
    names = "\n".join(f"  {item.class_id}: {item.code}" for item in CLASSES)
    data_yaml.write_text(
        f"path: {yolo_dir.resolve()}\ntrain: images/train\nval: images/val\n\nnames:\n{names}\n",
        encoding="utf-8",
    )
    (output / "README.md").write_text(
        "# Chassis Seed Dataset\n\n"
        "This is a weakly labeled bootstrap dataset collected from Wikimedia Commons. "
        "Labels are full-image weak boxes and must be replaced by human-reviewed bbox annotations for production quality.\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
