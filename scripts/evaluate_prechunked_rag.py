#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import resource
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate the platform retriever against a prechunked RAG suite.")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    manifest = args.manifest if args.manifest.is_absolute() else ROOT / args.manifest
    suite_path = args.suite if args.suite.is_absolute() else ROOT / args.suite
    output = args.out if args.out.is_absolute() else ROOT / args.out
    os.environ["RAG_PRECHUNKED_MANIFESTS"] = str(manifest)
    os.environ["RAG_ONLY_PRECHUNKED"] = "1"
    # English research prose needs a bounded word feature space at this scale;
    # character 2-4 grams caused heavy swapping in the initial stress run.
    os.environ.setdefault("RAG_TFIDF_ANALYZER", "word")

    from backend.app.services.rag import RagService

    service = RagService()
    index_started = time.perf_counter()
    status = service.status()
    index_seconds = time.perf_counter() - index_started
    suite = json.loads(suite_path.read_text(encoding="utf-8"))
    report = service.evaluate(suite["cases"])
    result = {
        "schema": "subjects_rag_retrieval_eval/v2",
        "suite_id": suite.get("suite_id"),
        "label_quality": suite.get("label_quality"),
        "corpus_manifest": str(manifest.relative_to(ROOT) if manifest.is_relative_to(ROOT) else manifest),
        "index": status,
        "index_build_seconds": round(index_seconds, 3),
        "peak_rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
        **report,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("suite_id", "index_build_seconds", "peak_rss_mb", "case_count", "metrics")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
