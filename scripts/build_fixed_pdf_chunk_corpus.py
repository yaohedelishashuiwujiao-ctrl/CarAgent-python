#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.services.pdf_chunking import PdfChunkingConfig, _extract_layout_pages, _remove_repeated_margins


PARSER_VERSION = "fixed-window-900-160-v1"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def split_text(text: str, size: int = 900, overlap: int = 160) -> list[str]:
    text = re.sub(r"\s+", " ", text).strip()
    parts: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + size)
        if end < len(text):
            boundary = max(text.rfind(mark, start + size // 2, end) for mark in "。！？；.!?;\n")
            if boundary > start:
                end = boundary + 1
        part = text[start:end].strip()
        if part:
            parts.append(part)
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return parts


def process(row: dict[str, Any], output: Path, timeout: float) -> dict[str, Any]:
    document_id = str(row["id"])
    sha = str(row.get("sha256") or "")
    cache = output / "documents" / f"{sha}.json"
    if cache.exists():
        try:
            payload = json.loads(cache.read_text(encoding="utf-8"))
            if payload.get("parser_version") == PARSER_VERSION:
                return {"status": "cached", "document_id": document_id, "cache_path": str(cache.relative_to(ROOT)), "chunk_count": len(payload["chunks"]), "source": row}
        except (OSError, json.JSONDecodeError, KeyError):
            pass
    try:
        artifact = ROOT / str(row["artifact_path"])
        pages = _extract_layout_pages(artifact, timeout_seconds=timeout)
        pages, _removed = _remove_repeated_margins(pages, PdfChunkingConfig())
        parts = split_text("\n".join(pages))
        chunks = []
        for ordinal, text in enumerate(parts):
            chunk_id = "fixed-" + hashlib.sha256(f"{document_id}:{ordinal}:{text}".encode()).hexdigest()[:20]
            chunks.append(
                {
                    "chunk_id": chunk_id,
                    "parent_id": "",
                    "ordinal": ordinal,
                    "text": text,
                    "page_start": "",
                    "page_end": "",
                    "section_path": [],
                    "content_types": ["fixed_window"],
                }
            )
        payload = {"document_id": document_id, "source_sha256": sha, "parser_version": PARSER_VERSION, "source": row, "chunks": chunks}
        temporary = cache.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, cache)
        return {"status": "ok", "document_id": document_id, "cache_path": str(cache.relative_to(ROOT)), "chunk_count": len(chunks), "source": row}
    except Exception as exc:
        return {"status": "error", "document_id": document_id, "error": f"{type(exc).__name__}: {exc}", "source": row}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the legacy fixed-window baseline over the PDF corpus.")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--timeout", type=float, default=90)
    args = parser.parse_args()
    manifest = args.manifest if args.manifest.is_absolute() else ROOT / args.manifest
    output = args.out if args.out.is_absolute() else ROOT / args.out
    (output / "documents").mkdir(parents=True, exist_ok=True)
    rows = [row for row in read_jsonl(manifest) if row.get("status") == "ok" and row.get("artifact_path")]
    records = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = [executor.submit(process, row, output, args.timeout) for row in rows]
        for index, future in enumerate(as_completed(futures), start=1):
            records.append(future.result())
            if index % 100 == 0 or index == len(rows):
                print(f"processed {index}/{len(rows)}", flush=True)
    records.sort(key=lambda row: row["document_id"])
    target = output / "manifest.jsonl"
    target.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in records), encoding="utf-8")
    summary = {
        "parser_version": PARSER_VERSION,
        "documents_requested": len(rows),
        "documents_succeeded": sum(row["status"] in {"ok", "cached"} for row in records),
        "documents_failed": sum(row["status"] == "error" for row in records),
        "chunks": sum(int(row.get("chunk_count") or 0) for row in records),
    }
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if summary["documents_failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
