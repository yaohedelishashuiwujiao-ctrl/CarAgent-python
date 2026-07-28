#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import time
from pathlib import Path

import requests
from requests import Response


API_URL = "https://commons.wikimedia.org/w/api.php"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download a curated bootstrap dataset from Wikimedia Commons.")
    parser.add_argument("--source-file", default="data/chassis_seed_sources.json")
    parser.add_argument("--output", default="resources/chassis_seed_dataset_curated")
    parser.add_argument("--sleep", type=float, default=0.5)
    parser.add_argument("--api-retries", type=int, default=3)
    parser.add_argument("--download-retries", type=int, default=2)
    parser.add_argument("--retry-sleep", type=float, default=3.0)
    parser.add_argument("--min-image-bytes", type=int, default=10_000)
    parser.add_argument("--weak-box-size", type=float, default=0.88)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    source = json.loads(Path(args.source_file).read_text(encoding="utf-8"))
    output = Path(args.output)
    raw_dir = output / "raw"
    yolo_dir = output / "yolo"
    output.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    session.headers.update({"User-Agent": "ChassisBenchmarkDatasetBuilder/0.2 (curated bootstrap)"})

    rows: list[dict] = []
    seen_sha256: set[str] = set()

    for class_item in source["classes"]:
        class_id = int(class_item["class_id"])
        class_code = class_item["class_code"]
        class_dir = raw_dir / class_code
        class_dir.mkdir(parents=True, exist_ok=True)
        collected = 0
        for title in class_item["titles"]:
            info = image_info(session, title, retries=args.api_retries, retry_sleep=args.retry_sleep)
            if not info:
                continue
            url = info.get("thumburl") or info.get("url", "")
            if not is_supported_image(url):
                continue
            try:
                content = download(session, url, retries=args.download_retries, retry_sleep=args.retry_sleep)
            except requests.RequestException:
                continue
            if len(content) < args.min_image_bytes:
                continue
            sha256 = hashlib.sha256(content).hexdigest()
            if sha256 in seen_sha256:
                continue
            seen_sha256.add(sha256)
            suffix = suffix_from_url(url)
            file_name = f"{class_code}_{collected + 1:04d}_{sha256[:10]}{suffix}"
            local_path = class_dir / file_name
            local_path.write_bytes(content)
            rows.append(
                {
                    "class_id": class_id,
                    "class_code": class_code,
                    "file_name": file_name,
                    "local_path": str(local_path),
                    "source": source.get("source", "wikimedia_commons"),
                    "source_title": title,
                    "source_url": url,
                    "license": info.get("extmetadata", {}).get("LicenseShortName", {}).get("value", ""),
                    "artist": strip_html(info.get("extmetadata", {}).get("Artist", {}).get("value", "")),
                    "credit": strip_html(info.get("extmetadata", {}).get("Credit", {}).get("value", "")),
                    "label_quality": "weak_auto_box",
                }
            )
            collected += 1
            time.sleep(args.sleep)
        print(f"{class_code}: collected {collected}", flush=True)

    write_manifest(output / "manifest.csv", rows)
    write_yolo(output, yolo_dir, source["classes"], rows, args.weak_box_size)
    print(f"manifest: {output / 'manifest.csv'}")
    print(f"images: {raw_dir}")
    print(f"yolo: {yolo_dir}")


def image_info(session: requests.Session, title: str, retries: int, retry_sleep: float) -> dict | None:
    params = {
        "action": "query",
        "format": "json",
        "titles": title,
        "prop": "imageinfo",
        "iiprop": "url|mime|extmetadata",
        "iiurlwidth": 1200,
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
    return suffix_from_url(url).lower() in {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"}


def suffix_from_url(url: str) -> str:
    suffix = Path(url.split("?", 1)[0]).suffix.lower()
    return ".jpg" if suffix == ".jpeg" else suffix


def strip_html(value: str) -> str:
    return value.replace("<br />", " ").replace("<br>", " ").strip()


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


def write_yolo(output: Path, yolo_dir: Path, classes: list[dict], rows: list[dict], weak_box_size: float) -> None:
    import shutil

    yolo_dir.mkdir(parents=True, exist_ok=True)
    for split in ["images/train", "images/val", "labels/train", "labels/val"]:
        (yolo_dir / split).mkdir(parents=True, exist_ok=True)

    for index, row in enumerate(rows):
        split = "train" if index % 5 != 4 else "val"
        image_src = Path(row["local_path"])
        image_dst = yolo_dir / "images" / split / image_src.name
        label_dst = yolo_dir / "labels" / split / f"{image_src.stem}.txt"
        shutil.copy2(image_src, image_dst)
        label_dst.write_text(f"{row['class_id']} 0.5 0.5 {weak_box_size:.6f} {weak_box_size:.6f}\n", encoding="utf-8")

    names = "\n".join(f"  {int(item['class_id'])}: {item['class_code']}" for item in sorted(classes, key=lambda item: int(item["class_id"])))
    data_yaml = yolo_dir / "chassis_parts_curated.yaml"
    data_yaml.write_text(
        f"path: {yolo_dir.resolve()}\ntrain: images/train\nval: images/val\n\nnames:\n{names}\n",
        encoding="utf-8",
    )
    (output / "README.md").write_text(
        "# Chassis Curated Seed Dataset\n\n"
        "This dataset is a curated bootstrap set from Wikimedia Commons. Labels are weak boxes and are only meant to bootstrap the first detection pipeline.\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
