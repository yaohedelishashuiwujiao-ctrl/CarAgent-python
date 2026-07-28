#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.services.pdf_chunking import PARSER_VERSION, PdfChunkingConfig, chunk_pdf


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)


def process(row: dict[str, Any], output_dir: Path, config: PdfChunkingConfig, timeout: float) -> dict[str, Any]:
    artifact = ROOT / str(row.get("artifact_path") or "")
    sha256 = str(row.get("sha256") or "")
    cache_path = output_dir / "documents" / f"{sha256}.json"
    if cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if cached.get("parser_version") == PARSER_VERSION and cached.get("config") == config.__dict__:
                return {"status": "cached", "cache_path": str(cache_path.relative_to(ROOT)), **_summary(cached), "source": row}
        except (OSError, json.JSONDecodeError):
            pass
    try:
        result = chunk_pdf(
            artifact,
            document_id=str(row["id"]),
            source_sha256=sha256,
            config=config,
            timeout_seconds=timeout,
        )
        payload = {**result.as_dict(), "source": row}
        write_json(cache_path, payload)
        return {"status": "ok", "cache_path": str(cache_path.relative_to(ROOT)), **_summary(payload), "source": row}
    except Exception as exc:
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}", "source": row}


def _summary(payload: dict[str, Any]) -> dict[str, Any]:
    quality = payload.get("quality") or {}
    chunks = payload.get("chunks") or []
    return {
        "document_id": payload.get("document_id"),
        "sha256": payload.get("source_sha256"),
        "page_count": quality.get("page_count"),
        "extracted_chars": quality.get("extracted_chars"),
        "chars_per_page": quality.get("chars_per_page"),
        "needs_ocr": quality.get("needs_ocr"),
        "removed_margin_lines": quality.get("removed_margin_lines"),
        "chunk_count": len(chunks),
        "table_chunk_count": sum(1 for chunk in chunks if "table" in (chunk.get("content_types") or [])),
        "sectioned_chunk_count": sum(1 for chunk in chunks if chunk.get("section_path")),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a resumable structure-aware PDF chunk corpus.")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--target-chars", type=int, default=1200)
    parser.add_argument("--max-chars", type=int, default=1800)
    parser.add_argument("--overlap-chars", type=int, default=180)
    args = parser.parse_args()

    manifest = args.manifest if args.manifest.is_absolute() else ROOT / args.manifest
    output_dir = args.out if args.out.is_absolute() else ROOT / args.out
    (output_dir / "documents").mkdir(parents=True, exist_ok=True)
    rows = [row for row in read_jsonl(manifest) if row.get("status") == "ok" and row.get("artifact_path")]
    if args.limit > 0:
        rows = rows[: args.limit]
    config = PdfChunkingConfig(args.target_chars, args.max_chars, args.overlap_chars)
    records: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers), thread_name_prefix="pdf-chunk") as executor:
        futures = {executor.submit(process, row, output_dir, config, args.timeout): row for row in rows}
        for index, future in enumerate(as_completed(futures), start=1):
            record = future.result()
            records.append(record)
            print(
                f"{record['status']} {index}/{len(rows)} {record.get('document_id') or record['source'].get('id')} "
                f"pages={record.get('page_count', '-')} chunks={record.get('chunk_count', '-')} "
                f"{record.get('error', '')}",
                flush=True,
            )
    records.sort(key=lambda item: str(item.get("document_id") or item.get("source", {}).get("id") or ""))
    manifest_path = output_dir / "manifest.jsonl"
    temporary = manifest_path.with_suffix(".jsonl.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    os.replace(temporary, manifest_path)

    successful = [record for record in records if record["status"] in {"ok", "cached"}]
    chunk_counts = [int(record.get("chunk_count") or 0) for record in successful]
    summary = {
        "parser_version": PARSER_VERSION,
        "config": config.__dict__,
        "documents_requested": len(rows),
        "documents_succeeded": len(successful),
        "documents_failed": len(rows) - len(successful),
        "documents_needing_ocr": sum(bool(record.get("needs_ocr")) for record in successful),
        "pages": sum(int(record.get("page_count") or 0) for record in successful),
        "extracted_chars": sum(int(record.get("extracted_chars") or 0) for record in successful),
        "chunks": sum(chunk_counts),
        "table_chunks": sum(int(record.get("table_chunk_count") or 0) for record in successful),
        "sectioned_chunks": sum(int(record.get("sectioned_chunk_count") or 0) for record in successful),
        "median_chunks_per_document": round(statistics.median(chunk_counts), 2) if chunk_counts else 0,
    }
    write_json(output_dir / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True), flush=True)
    return 0 if summary["documents_failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
