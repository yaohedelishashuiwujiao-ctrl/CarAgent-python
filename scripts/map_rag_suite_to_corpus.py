#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9][a-z0-9_.-]{1,}", text.lower()))


def load_documents(manifest: Path, wanted: set[str]) -> dict[str, dict[str, Any]]:
    result = {}
    for row in rows(manifest):
        document_id = str(row.get("document_id") or row.get("source", {}).get("id") or "")
        if document_id not in wanted or row.get("status") not in {"ok", "cached"}:
            continue
        cache = Path(str(row["cache_path"]))
        cache = cache if cache.is_absolute() else ROOT / cache
        result[document_id] = json.loads(cache.read_text(encoding="utf-8"))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Map evidence labels from one chunking strategy to another.")
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--target-manifest", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    resolve = lambda path: path if path.is_absolute() else ROOT / path
    suite = json.loads(resolve(args.suite).read_text(encoding="utf-8"))
    wanted_docs = {str(case["expected_document_ids"][0]) for case in suite["cases"]}
    source_docs = load_documents(resolve(args.source_manifest), wanted_docs)
    target_docs = load_documents(resolve(args.target_manifest), wanted_docs)
    mapped = []
    for case in suite["cases"]:
        document_id = str(case["expected_document_ids"][0])
        source_chunk_id = str(case["expected_chunk_ids"][0])
        source_chunks = {str(chunk["chunk_id"]): chunk for chunk in source_docs[document_id]["chunks"]}
        evidence_tokens = tokens(str(source_chunks[source_chunk_id]["text"]))
        ranked = []
        for chunk in target_docs[document_id]["chunks"]:
            candidate_tokens = tokens(str(chunk["text"]))
            overlap = len(evidence_tokens & candidate_tokens)
            coverage = overlap / max(1, len(evidence_tokens))
            precision = overlap / max(1, len(candidate_tokens))
            ranked.append((0.7 * coverage + 0.3 * precision, chunk))
        ranked.sort(key=lambda item: item[0], reverse=True)
        relevant = [ranked[0]]
        if len(ranked) > 1 and ranked[1][0] >= ranked[0][0] * 0.75:
            relevant.append(ranked[1])
        mapped_ids = [str(item[1]["chunk_id"]) for item in relevant]
        mapped.append(
            {
                **case,
                "expected_chunk_ids": mapped_ids,
                "relevance": {chunk_id: 2 if index == 0 else 1 for index, chunk_id in enumerate(mapped_ids)},
                "label_mapping": {
                    "source_chunk_id": source_chunk_id,
                    "target_scores": {str(item[1]["chunk_id"]): round(item[0], 4) for item in relevant},
                },
            }
        )
    output = resolve(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            {
                **suite,
                "suite_id": f"{suite['suite_id']}-mapped-fixed-window",
                "description": str(suite.get("description") or "") + " Evidence labels mapped within the same source document by token coverage for the fixed-window ablation.",
                "cases": mapped,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"cases": len(mapped), "one_label": sum(len(case["expected_chunk_ids"]) == 1 for case in mapped), "two_labels": sum(len(case["expected_chunk_ids"]) == 2 for case in mapped)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
