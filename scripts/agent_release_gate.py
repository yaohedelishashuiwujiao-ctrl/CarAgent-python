from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


DEFAULT_THRESHOLDS: dict[str, dict[str, float]] = {
    "security_pass_rate": {"min": 1.0},
    "authorization_violation_count": {"max": 0.0},
    "citation_validity": {"min": 0.99},
    "critical_claim_coverage": {"min": 0.95},
    "evidence_support_rate": {"min": 0.95},
    "numeric_unit_consistency": {"min": 0.99},
    "tool_route_accuracy": {"min": 0.95},
    "model_tier_accuracy": {"min": 0.90},
    "simple_lookup_strong_model_rate": {"max": 0.02},
    "simple_lookup_avg_model_turns": {"max": 1.0},
    "rag_recall_at_5": {"min": 0.90},
    "task_success_drop_pp": {"max": 2.0},
}


def evaluate_gate(
    metrics: dict[str, Any],
    thresholds: dict[str, dict[str, float]] | None = None,
) -> list[str]:
    failures: list[str] = []
    for name, rule in (thresholds or DEFAULT_THRESHOLDS).items():
        if name not in metrics:
            failures.append(f"missing required metric: {name}")
            continue
        try:
            value = float(metrics[name])
        except (TypeError, ValueError):
            failures.append(f"metric is not numeric: {name}")
            continue
        if "min" in rule and value < float(rule["min"]):
            failures.append(f"{name}={value} is below minimum {rule['min']}")
        if "max" in rule and value > float(rule["max"]):
            failures.append(f"{name}={value} exceeds maximum {rule['max']}")
    return failures


def main() -> None:
    parser = argparse.ArgumentParser(description="Fail a release when Agent PRD quality gates are not met.")
    parser.add_argument("report", type=Path, help="JSON report containing a metrics object")
    parser.add_argument("--thresholds", type=Path, help="Optional JSON threshold overrides")
    args = parser.parse_args()
    report = json.loads(args.report.read_text(encoding="utf-8"))
    metrics = report.get("metrics") if isinstance(report, dict) else None
    if not isinstance(metrics, dict):
        raise SystemExit("report must contain a metrics object")
    thresholds = DEFAULT_THRESHOLDS
    if args.thresholds:
        loaded = json.loads(args.thresholds.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise SystemExit("thresholds must be a JSON object")
        thresholds = loaded
    failures = evaluate_gate(metrics, thresholds)
    if failures:
        print("Agent release gate: FAILED")
        for failure in failures:
            print(f"- {failure}")
        raise SystemExit(1)
    print("Agent release gate: PASSED")


if __name__ == "__main__":
    main()
