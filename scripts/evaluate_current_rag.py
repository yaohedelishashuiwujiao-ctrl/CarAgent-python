#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import resource
import sys
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _resolve(path: Path) -> Path:
    return path if path.is_absolute() else ROOT / path


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate the current LlamaIndex Agentic RAG retriever.")
    parser.add_argument("--suite", type=Path, default=Path("evals/suites/rag_pilot_v1.json"))
    parser.add_argument("--out", type=Path, default=Path("evals/results/llamaindex_light_rag_pilot_v1.json"))
    parser.add_argument("--env-file", type=Path, default=Path(".env.local"))
    args = parser.parse_args()

    env_file = _resolve(args.env_file)
    if env_file.exists():
        load_dotenv(env_file)

    from backend.app.services.rag import rag_service

    suite_path = _resolve(args.suite)
    output_path = _resolve(args.out)
    suite = json.loads(suite_path.read_text(encoding="utf-8"))
    cases = suite.get("cases")
    if not isinstance(cases, list) or not cases:
        raise SystemExit("suite must contain a non-empty cases list")

    started = time.perf_counter()
    status = rag_service.status()
    report = rag_service.evaluate(cases)
    result: dict[str, Any] = {
        "schema": "subjects_rag_retrieval_eval/v3",
        "suite_id": suite.get("suite_id"),
        "description": suite.get("description"),
        "label_quality": suite.get("label_quality", "pilot_not_human_held_out"),
        "suite_path": str(suite_path.relative_to(ROOT) if suite_path.is_relative_to(ROOT) else suite_path),
        "index": status,
        "wall_seconds": round(time.perf_counter() - started, 3),
        "peak_rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
        **report,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(output_path.relative_to(ROOT) if output_path.is_relative_to(ROOT) else output_path),
                "suite_id": result["suite_id"],
                "case_count": result["case_count"],
                "metrics": result["metrics"],
                "wall_seconds": result["wall_seconds"],
                "peak_rss_mb": result["peak_rss_mb"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
