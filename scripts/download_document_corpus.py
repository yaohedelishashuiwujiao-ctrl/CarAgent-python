#!/usr/bin/env python3
"""Resumable, rate-limited downloader for a CSV document corpus.

The CSV must contain ``id`` and ``official_url`` columns.  Source metadata is
preserved in a JSONL manifest.  Only valid PDF payloads are accepted for now;
files are content-addressed by SHA-256 so duplicate payloads share one artifact.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
USER_AGENT = "SubjectsAgent-Platform/0.1 public-document-corpus"


class StartRateLimiter:
    def __init__(self, interval_seconds: float) -> None:
        self.interval = max(0.0, interval_seconds)
        self._next_start = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            delay = max(0.0, self._next_start - now)
            self._next_start = max(now, self._next_start) + self.interval
        if delay:
            time.sleep(delay)


def load_manifest(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    records: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record.get("id"):
            records[str(record["id"])] = record
    return records


def save_manifest(path: Path, rows: list[dict[str, str]], records: dict[str, dict[str, Any]]) -> None:
    temporary = path.with_suffix(".jsonl.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            record = records.get(row["id"])
            if record is not None:
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    os.replace(temporary, path)


def valid_existing(record: dict[str, Any]) -> bool:
    artifact = str(record.get("artifact_path") or "")
    return record.get("status") == "ok" and bool(artifact) and (ROOT / artifact).is_file()


def download_one(
    row: dict[str, str],
    *,
    artifact_dir: Path,
    temporary_dir: Path,
    timeout: float,
    max_bytes: int,
    attempts: int,
    limiter: StartRateLimiter,
) -> dict[str, Any]:
    started = time.time()
    row_id = row["id"]
    url = row["official_url"]
    last_error = ""
    for attempt in range(1, attempts + 1):
        temporary = temporary_dir / f"{hashlib.sha256(row_id.encode()).hexdigest()[:16]}-{attempt}.part"
        try:
            limiter.wait()
            request = urllib.request.Request(
                url,
                headers={"User-Agent": USER_AGENT, "Accept": "application/pdf,*/*;q=0.1"},
            )
            digest = hashlib.sha256()
            total = 0
            prefix = b""
            with urllib.request.urlopen(request, timeout=timeout) as response, temporary.open("wb") as handle:
                declared = int(response.headers.get("Content-Length") or 0)
                if declared > max_bytes:
                    raise ValueError(f"declared size {declared} exceeds limit {max_bytes}")
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    if len(prefix) < 16:
                        prefix += chunk[: 16 - len(prefix)]
                    total += len(chunk)
                    if total > max_bytes:
                        raise ValueError(f"download exceeded size limit {max_bytes}")
                    digest.update(chunk)
                    handle.write(chunk)
                final_url = response.geturl()
                content_type = response.headers.get("Content-Type", "")
            if not prefix.startswith(b"%PDF-"):
                raise ValueError(f"payload is not a PDF; content-type={content_type!r}")
            if total < 10_000:
                raise ValueError(f"PDF payload is unexpectedly small: {total} bytes")
            sha256 = digest.hexdigest()
            artifact = artifact_dir / f"{sha256[:16]}.pdf"
            if artifact.exists():
                temporary.unlink(missing_ok=True)
                deduplicated = True
            else:
                os.replace(temporary, artifact)
                deduplicated = False
            return {
                **row,
                "status": "ok",
                "status_code": 200,
                "content_type": content_type,
                "final_url": final_url,
                "sha256": sha256,
                "bytes": total,
                "artifact_path": str(artifact.relative_to(ROOT)),
                "deduplicated_payload": deduplicated,
                "attempt_count": attempt,
                "downloaded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "elapsed_ms": int((time.time() - started) * 1000),
            }
        except urllib.error.HTTPError as exc:
            last_error = f"HTTP {exc.code}: {exc.reason}"
            retryable = exc.code == 429 or 500 <= exc.code < 600
            if not retryable or attempt >= attempts:
                break
            time.sleep(min(30.0, 2.0 ** attempt))
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt >= attempts:
                break
            time.sleep(min(30.0, 2.0 ** attempt))
        finally:
            temporary.unlink(missing_ok=True)
    return {
        **row,
        "status": "error",
        "error": last_error,
        "attempt_count": attempts,
        "downloaded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "elapsed_ms": int((time.time() - started) * 1000),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Download and validate a public PDF corpus.")
    parser.add_argument("--csv", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--max", type=int, default=0)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--request-interval", type=float, default=0.5)
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument("--max-mib", type=int, default=100)
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--checkpoint-every", type=int, default=10)
    args = parser.parse_args()

    csv_path = args.csv if args.csv.is_absolute() else ROOT / args.csv
    out_dir = args.out if args.out.is_absolute() else ROOT / args.out
    artifact_dir = out_dir / "artifacts"
    temporary_dir = out_dir / ".tmp"
    out_dir.mkdir(parents=True, exist_ok=True)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    temporary_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "manifest.jsonl"

    rows = list(csv.DictReader(csv_path.open(encoding="utf-8")))
    if args.max > 0:
        rows = rows[: args.max]
    if any(not row.get("id") or not row.get("official_url") for row in rows):
        raise SystemExit("every CSV row must contain id and official_url")

    records = load_manifest(manifest_path)
    pending = [row for row in rows if not valid_existing(records.get(row["id"], {}))]
    skipped = len(rows) - len(pending)
    print(f"rows={len(rows)} resumed_ok={skipped} pending={len(pending)}", flush=True)
    limiter = StartRateLimiter(args.request_interval)
    lock = threading.Lock()
    completed = 0
    with ThreadPoolExecutor(max_workers=max(1, args.workers), thread_name_prefix="corpus-download") as executor:
        futures = {
            executor.submit(
                download_one,
                row,
                artifact_dir=artifact_dir,
                temporary_dir=temporary_dir,
                timeout=args.timeout,
                max_bytes=max(1, args.max_mib) * 1024 * 1024,
                attempts=max(1, args.attempts),
                limiter=limiter,
            ): row
            for row in pending
        }
        for future in as_completed(futures):
            record = future.result()
            with lock:
                records[record["id"]] = record
                completed += 1
                print(
                    f"{record['status']} {skipped + completed}/{len(rows)} {record['id']} "
                    f"{record.get('bytes', record.get('error', ''))}",
                    flush=True,
                )
                if completed % max(1, args.checkpoint_every) == 0:
                    save_manifest(manifest_path, rows, records)
    save_manifest(manifest_path, rows, records)
    ok = sum(1 for row in rows if valid_existing(records.get(row["id"], {})))
    failed = len(rows) - ok
    print(f"rows={len(rows)} ok={ok} failed={failed} manifest={manifest_path}", flush=True)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
