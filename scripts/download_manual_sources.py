#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import mimetypes
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CSV = ROOT / "resources" / "manual_corpus" / "official_cn_vehicle_manual_sources.csv"
DEFAULT_OUT = ROOT / "resources" / "manual_corpus" / "downloads"


def extension_from_content_type(content_type: str, url: str) -> str:
    guessed = mimetypes.guess_extension(content_type.split(";")[0].strip())
    if guessed in {".htm", ".html", ".pdf", ".txt", ".json"}:
        return ".html" if guessed == ".htm" else guessed
    suffix = Path(url.split("?", 1)[0]).suffix.lower()
    if suffix in {".html", ".htm", ".pdf", ".txt", ".json"}:
        return ".html" if suffix == ".htm" else suffix
    return ".html"


def normalize_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    path = urllib.parse.quote(parsed.path, safe="/%")
    query = urllib.parse.quote(parsed.query, safe="=&%?/+,:;@")
    fragment = urllib.parse.quote(parsed.fragment, safe="")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, query, fragment))


def read_existing_manifest(path: Path) -> dict[str, dict[str, object]]:
    if not path.exists():
        return {}
    records: dict[str, dict[str, object]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        row_id = str(record.get("id") or "")
        if row_id:
            records[row_id] = record
    return records


def request_url(url: str, timeout: float) -> tuple[int, str, bytes, str]:
    url = normalize_url(url)
    req = urllib.request.Request(
        url,
        method="GET",
        headers={
            "User-Agent": "Mozilla/5.0 subjects-agent-manual-downloader/0.1",
            "Accept": "text/html,application/xhtml+xml,application/pdf,*/*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        content_type = resp.headers.get("Content-Type", "")
        final_url = resp.geturl()
        data = resp.read()
        return int(resp.status), content_type, data, final_url


def save_manifest(path: Path, records: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Download official vehicle manual source pages/files.")
    parser.add_argument("--csv", default=str(DEFAULT_CSV), help="Source CSV path.")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="Download output directory.")
    parser.add_argument("--max", type=int, default=0, help="Download at most N rows. 0 means all rows.")
    parser.add_argument("--timeout", type=float, default=20.0, help="Per-request timeout in seconds.")
    parser.add_argument("--delay", type=float, default=0.4, help="Delay between unique URL downloads.")
    parser.add_argument("--force", action="store_true", help="Redownload even when an artifact exists.")
    parser.add_argument(
        "--source-type",
        choices=["html_manual", "manual_portal", "manual_portal_cn"],
        help="Filter by source_type.",
    )
    args = parser.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.is_absolute():
        csv_path = ROOT / csv_path
    out_dir = Path(args.out)
    if not out_dir.is_absolute():
        out_dir = ROOT / out_dir
    artifact_dir = out_dir / "artifacts"
    out_dir.mkdir(parents=True, exist_ok=True)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "manifest.jsonl"

    rows = list(csv.DictReader(csv_path.open(encoding="utf-8")))
    if args.source_type:
        rows = [row for row in rows if row.get("source_type") == args.source_type]
    if args.max > 0:
        rows = rows[: args.max]

    existing_by_id = read_existing_manifest(manifest_path)
    url_cache: dict[str, dict[str, object]] = {}
    records: list[dict[str, object]] = []
    ok = 0

    for row in rows:
        row_id = row["id"]
        url = row["official_url"]
        previous = existing_by_id.get(row_id)
        if previous and not args.force and previous.get("status") == "ok":
            artifact = previous.get("artifact_path")
            if artifact and (ROOT / str(artifact)).exists():
                records.append(previous)
                ok += 1
                print(f"SKIP ok {row_id} {artifact}")
                continue

        if url in url_cache and not args.force:
            base = dict(url_cache[url])
            record = {
                **row,
                **base,
                "id": row_id,
                "deduped_from_url": True,
            }
            records.append(record)
            ok += int(record.get("status") == "ok")
            print(f"DEDUP {record.get('status')} {row_id} {record.get('artifact_path', '')}")
            continue

        started = time.time()
        try:
            status_code, content_type, data, final_url = request_url(url, args.timeout)
            digest = hashlib.sha256(data).hexdigest()
            ext = extension_from_content_type(content_type, final_url)
            artifact_path = artifact_dir / f"{digest[:16]}{ext}"
            if args.force or not artifact_path.exists():
                artifact_path.write_bytes(data)
            status = "ok" if 200 <= status_code < 400 else "http_error"
            record = {
                **row,
                "status": status,
                "status_code": status_code,
                "content_type": content_type,
                "final_url": final_url,
                "sha256": digest,
                "bytes": len(data),
                "artifact_path": str(artifact_path.relative_to(ROOT)),
                "downloaded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "elapsed_ms": int((time.time() - started) * 1000),
            }
        except urllib.error.HTTPError as exc:
            record = {
                **row,
                "status": "http_error",
                "status_code": exc.code,
                "error": str(exc.reason),
                "downloaded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "elapsed_ms": int((time.time() - started) * 1000),
            }
        except Exception as exc:
            record = {
                **row,
                "status": "error",
                "error": str(exc),
                "downloaded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "elapsed_ms": int((time.time() - started) * 1000),
            }

        records.append(record)
        url_cache[url] = {
            key: value
            for key, value in record.items()
            if key
            not in {
                "id",
                "brand",
                "model",
                "year",
                "market",
                "language",
                "source_type",
                "official_url",
                "selector_hint",
            }
        }
        ok += int(record.get("status") == "ok")
        print(f"{record.get('status')} {record.get('status_code', '-')} {row_id} {record.get('artifact_path', record.get('error', ''))}")
        time.sleep(max(0.0, args.delay))

    save_manifest(manifest_path, records)
    print(f"rows={len(records)} ok={ok} failed={len(records) - ok} manifest={manifest_path}")
    return 0 if ok == len(records) else 1


if __name__ == "__main__":
    sys.exit(main())
