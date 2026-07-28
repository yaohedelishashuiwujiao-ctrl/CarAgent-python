from __future__ import annotations

import argparse
import hashlib
import json
import random
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TERMINAL = {"succeeded", "failed", "cancelled", "rejected"}


def request_json(url: str, *, method: str = "GET", payload: dict[str, Any] | None = None, headers: dict[str, str] | None = None, timeout: float = 30) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    merged = {"Accept": "application/json", **(headers or {})}
    if body is not None:
        merged["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=merged, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def request_text(url: str, *, headers: dict[str, str], timeout: float = 30) -> str:
    request = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8")


def parse_sse(raw: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for frame in raw.replace("\r\n", "\n").split("\n\n"):
        data = "\n".join(line[5:].lstrip() for line in frame.splitlines() if line.startswith("data:"))
        if not data:
            continue
        try:
            envelope = json.loads(data)
        except json.JSONDecodeError:
            continue
        payload = envelope.get("payload") if isinstance(envelope.get("payload"), dict) else {}
        events.append({"type": str(envelope.get("event_type") or "message"), **payload})
    return events


def run_current_case(base_url: str, case: dict[str, Any], repetition: int, timeout_s: float) -> dict[str, Any]:
    user_id = "agent-eval"
    tenant_id = "default"
    headers = {"X-User-ID": user_id, "X-Tenant-ID": tenant_id}
    run_id = uuid.uuid4().hex
    started = time.time()
    created = request_json(
        f"{base_url}/api/agent/chat_jobs",
        method="POST",
        payload={"session_id": f"eval-{case['id']}-{run_id}", "prompt": case["prompt"]},
        headers={**headers, "Idempotency-Key": f"eval-{case['id']}-{repetition}-{run_id}"},
    )
    job_id = str(created["job_id"])
    deadline = time.time() + timeout_s
    job: dict[str, Any] = {}
    while time.time() < deadline:
        job = request_json(f"{base_url}/api/agent/chat_jobs/{job_id}", headers=headers)
        if str(job.get("status")) in TERMINAL:
            break
        time.sleep(0.5)
    else:
        try:
            request_json(f"{base_url}/api/agent/chat_jobs/{job_id}/cancel", method="POST", headers=headers)
        except Exception:
            pass
        raise TimeoutError(f"case {case['id']} exceeded {timeout_s}s")

    raw_events = request_text(f"{base_url}/api/agent/chat_jobs/{job_id}/events?after_seq=0", headers=headers)
    events = parse_sse(raw_events)
    metadata = job.get("final_metadata") if isinstance(job.get("final_metadata"), dict) else {}
    usage = job.get("usage") if isinstance(job.get("usage"), dict) else {}
    created_at = _number(job.get("created_at"))
    started_at = _number(job.get("started_at"))
    finished_at = _number(job.get("finished_at"))
    return {
        "case_id": case["id"],
        "repetition": repetition,
        "target": "current",
        "job_id": job_id,
        "status": job.get("status"),
        "error_message": job.get("error_message"),
        "final_text": str(job.get("final_text") or ""),
        "usage": usage,
        "queue_ms": round((started_at - created_at) * 1000, 3) if started_at and created_at else None,
        "run_ms": round((finished_at - started_at) * 1000, 3) if finished_at and started_at else None,
        "wall_ms": round((time.time() - started) * 1000, 3),
        "events": events,
        "final_metadata": metadata,
    }


def run_upstream_case(case: dict[str, Any], repetition: int, timeout_s: float, model: str) -> dict[str, Any]:
    adapter = Path(__file__).resolve().parent / "adapters" / "upstream_clawd.py"
    root = Path("/tmp/subjects-agent-evals") / "upstream" / f"{case['id']}-{repetition}-{uuid.uuid4().hex}"
    prompt_path = root / "prompt.txt"
    workspace = root / "workspace"
    root.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text(str(case["prompt"]), encoding="utf-8")
    started = time.time()
    completed = subprocess.run(
        [
            sys.executable,
            str(adapter),
            "--prompt-file",
            str(prompt_path),
            "--workspace",
            str(workspace),
            "--model",
            model,
        ],
        cwd=adapter.parents[2] / "experiments" / "clawd-upstream",
        text=True,
        capture_output=True,
        timeout=timeout_s,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or f"upstream exited {completed.returncode}")
    payload = json.loads(completed.stdout.strip().splitlines()[-1])
    return {
        "case_id": case["id"],
        "repetition": repetition,
        "target": "upstream",
        "status": payload.get("status"),
        "error_message": None,
        "final_text": str(payload.get("final_text") or ""),
        "usage": payload.get("usage") if isinstance(payload.get("usage"), dict) else {},
        "queue_ms": None,
        "run_ms": payload.get("run_ms"),
        "wall_ms": round((time.time() - started) * 1000, 3),
        "events": payload.get("events") if isinstance(payload.get("events"), list) else [],
        "final_metadata": {
            "run_dir": str(root),
            "artifacts": payload.get("artifacts") if isinstance(payload.get("artifacts"), list) else [],
            "turns": payload.get("turns"),
            "model": payload.get("model"),
        },
    }


def score_case(case: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    checks = case.get("checks") or {}
    text = str(result.get("final_text") or "")
    events = result.get("events") if isinstance(result.get("events"), list) else []
    metadata = result.get("final_metadata") if isinstance(result.get("final_metadata"), dict) else {}
    tool_uses = [event for event in events if event.get("type") == "tool_use"]
    tool_results = [event for event in events if event.get("type") == "tool_result"]
    tool_names = [str(event.get("tool") or "") for event in tool_uses]
    successful_names = [
        str(event.get("tool") or "")
        for event in tool_results
        if str(event.get("status") or "ok") != "error"
    ]
    assertions: list[dict[str, Any]] = []

    def add(name: str, passed: bool, detail: Any) -> None:
        assertions.append({"name": name, "passed": bool(passed), "detail": detail})

    if checks.get("terminal_status"):
        allowed = [str(item) for item in checks["terminal_status"]]
        add("terminal_status", str(result.get("status")) in allowed, {"actual": result.get("status"), "allowed": allowed})
    for required in checks.get("required_text") or []:
        add(f"required_text:{required}", str(required).lower() in text.lower(), required)
    if checks.get("required_text_any"):
        candidates = [str(item) for item in checks["required_text_any"]]
        add("required_text_any", any(item.lower() in text.lower() for item in candidates), candidates)
    for forbidden in checks.get("forbidden_text") or []:
        add(f"forbidden_text:{forbidden}", str(forbidden).lower() not in text.lower(), forbidden)
    if checks.get("required_tool_any"):
        candidates = [str(item) for item in checks["required_tool_any"]]
        add("required_tool_any", any(item in tool_names for item in candidates), {"required": candidates, "actual": tool_names})
    if checks.get("required_tool_all"):
        candidates = [str(item) for item in checks["required_tool_all"]]
        add("required_tool_all", all(item in tool_names for item in candidates), {"required": candidates, "actual": tool_names})
    for forbidden in checks.get("forbidden_tool") or []:
        add(f"forbidden_tool:{forbidden}", forbidden not in tool_names, tool_names)
    for forbidden in checks.get("forbidden_successful_tool") or []:
        add(f"forbidden_successful_tool:{forbidden}", forbidden not in successful_names, successful_names)
    if checks.get("citation_required"):
        citations = metadata.get("citations") if isinstance(metadata.get("citations"), list) else []
        add("citation_required", bool(citations) and "[" in text, len(citations))
    if checks.get("task_contract_status"):
        actual = str(metadata.get("task_contract_status") or "")
        add("task_contract_status", actual == checks["task_contract_status"], actual)
    if checks.get("expected_route"):
        actual = str(metadata.get("route") or "")
        add("expected_route", actual == str(checks["expected_route"]), actual)
    if checks.get("expected_model_tier"):
        actual = str(metadata.get("model_tier") or "")
        add("expected_model_tier", actual == str(checks["expected_model_tier"]), actual)
    if checks.get("expected_budget_class"):
        actual = str(metadata.get("budget_class") or "")
        add("expected_budget_class", actual == str(checks["expected_budget_class"]), actual)
    if checks.get("plan_complete") is not None:
        run_state = metadata.get("run_state") if isinstance(metadata.get("run_state"), dict) else {}
        plan = run_state.get("plan") if isinstance(run_state.get("plan"), list) else []
        complete = bool(plan) and all(item.get("status") == "completed" for item in plan if isinstance(item, dict))
        add("plan_complete", complete is bool(checks["plan_complete"]), plan)
    if checks.get("parallel_batch_min") is not None:
        audit = metadata.get("tool_audit") if isinstance(metadata.get("tool_audit"), list) else []
        count = sum(1 for event in audit if event.get("event") == "parallel_tool_batch_completed")
        add("parallel_batch_min", count >= int(checks["parallel_batch_min"]), count)
    if checks.get("max_tool_calls") is not None:
        add("max_tool_calls", len(tool_uses) <= int(checks["max_tool_calls"]), len(tool_uses))
    if checks.get("artifact_type"):
        artifact = _find_artifact(metadata, str(checks["artifact_type"]))
        add("artifact_exists", artifact is not None and artifact.is_file() and artifact.stat().st_size > 0, str(artifact) if artifact else None)
        if artifact and checks.get("artifact_slide_count") is not None:
            count = _pptx_slide_count(artifact)
            add("artifact_slide_count", count == int(checks["artifact_slide_count"]), count)

    invalid_results = sum(1 for event in tool_results if str(event.get("outcome_status") or "") == "invalid_input")
    score = sum(1 for item in assertions if item["passed"]) / len(assertions) if assertions else 0.0
    return {
        "passed": bool(assertions) and all(item["passed"] for item in assertions),
        "score": round(score, 4),
        "assertions": assertions,
        "tool_calls": len(tool_uses),
        "invalid_tool_results": invalid_results,
        "invalid_tool_rate": round(invalid_results / max(1, len(tool_uses)), 4),
        "route": str(metadata.get("route") or ""),
        "model_tier": str(metadata.get("model_tier") or ""),
        "budget_class": str(metadata.get("budget_class") or ""),
    }


def _find_artifact(metadata: dict[str, Any], artifact_type: str) -> Path | None:
    requirements = metadata.get("requirements") if isinstance(metadata.get("requirements"), list) else []
    for requirement in requirements:
        if not isinstance(requirement, dict) or requirement.get("id") != f"artifact:{artifact_type}":
            continue
        paths = requirement.get("artifact_paths") if isinstance(requirement.get("artifact_paths"), list) else []
        if paths:
            return Path(str(paths[-1]))
    for artifact in metadata.get("artifacts") or []:
        if not isinstance(artifact, dict):
            continue
        path = Path(str(artifact.get("path") or ""))
        if path.suffix.lower() == f".{artifact_type.lower()}":
            return path
    return None


def _pptx_slide_count(path: Path) -> int | None:
    try:
        with zipfile.ZipFile(path) as archive:
            return sum(1 for name in archive.namelist() if name.startswith("ppt/slides/slide") and name.endswith(".xml"))
    except (OSError, zipfile.BadZipFile):
        return None


def aggregate(records: list[dict[str, Any]], *, input_price: float, output_price: float) -> dict[str, Any]:
    scored = [record["evaluation"] for record in records]
    input_tokens = [int(record.get("usage", {}).get("input_tokens") or 0) for record in records]
    output_tokens = [int(record.get("usage", {}).get("output_tokens") or 0) for record in records]
    route_assertions = _assertions_named(scored, "expected_route")
    model_tier_assertions = _assertions_named(scored, "expected_model_tier")
    simple_records = [
        record for record in records
        if int((record.get("case") or {}).get("difficulty") or 0) <= 1
    ]
    simple_strong = [
        record for record in simple_records
        if str((record.get("evaluation") or {}).get("model_tier") or "") == "strong"
    ]
    model_turns = [
        int(((record.get("final_metadata") or {}).get("run_budget") or {}).get("usage", {}).get("model_turns") or 0)
        for record in records
        if isinstance(record.get("final_metadata"), dict)
    ]
    return {
        "runs": len(records),
        "pass_rate": round(sum(1 for item in scored if item["passed"]) / max(1, len(scored)), 4),
        "mean_score": round(statistics.fmean(item["score"] for item in scored), 4) if scored else 0.0,
        "median_queue_ms": _median(record.get("queue_ms") for record in records),
        "median_run_ms": _median(record.get("run_ms") for record in records),
        "total_input_tokens": sum(input_tokens),
        "total_output_tokens": sum(output_tokens),
        "estimated_cost_yuan": round(sum(input_tokens) / 1000 * input_price + sum(output_tokens) / 1000 * output_price, 4),
        "tool_calls": sum(int(item.get("tool_calls") or 0) for item in scored),
        "invalid_tool_results": sum(int(item.get("invalid_tool_results") or 0) for item in scored),
        "tool_route_accuracy": _pass_rate(route_assertions),
        "model_tier_accuracy": _pass_rate(model_tier_assertions),
        "simple_lookup_strong_model_rate": round(len(simple_strong) / max(1, len(simple_records)), 4),
        "simple_lookup_avg_model_turns": round(statistics.fmean(model_turns), 4) if model_turns else 0.0,
    }


def _assertions_named(scored: list[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    assertions: list[dict[str, Any]] = []
    for item in scored:
        for assertion in item.get("assertions") or []:
            if isinstance(assertion, dict) and assertion.get("name") == name:
                assertions.append(assertion)
    return assertions


def _pass_rate(assertions: list[dict[str, Any]]) -> float:
    if not assertions:
        return 1.0
    return round(sum(1 for item in assertions if item.get("passed")) / len(assertions), 4)


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _median(values: Any) -> float | None:
    items = [float(value) for value in values if value is not None]
    return round(statistics.median(items), 3) if items else None


def main() -> None:
    parser = argparse.ArgumentParser(description="Run reproducible SubjectsAgent evaluations.")
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--target", choices=("current", "upstream"), default="current")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--model", default="deepseek-v4-flash-260425")
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--timeout-seconds", type=float, default=600)
    parser.add_argument("--seed", type=int, default=20260717)
    parser.add_argument("--input-price-per-1k", type=float, default=0.006)
    parser.add_argument("--output-price-per-1k", type=float, default=0.030)
    parser.add_argument("--case", action="append", dest="case_ids")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    raw_suite = args.suite.read_bytes()
    suite = json.loads(raw_suite.decode("utf-8"))
    cases = [case for case in suite.get("cases", []) if not args.case_ids or case.get("id") in set(args.case_ids)]
    schedule = [(case, repetition) for repetition in range(1, max(1, args.repetitions) + 1) for case in cases]
    random.Random(args.seed).shuffle(schedule)
    records: list[dict[str, Any]] = []
    for case, repetition in schedule:
        try:
            if args.target == "current":
                record = run_current_case(args.base_url.rstrip("/"), case, repetition, args.timeout_seconds)
            else:
                record = run_upstream_case(case, repetition, args.timeout_seconds, args.model)
        except Exception as exc:
            record = {
                "case_id": case["id"],
                "repetition": repetition,
                "target": args.target,
                "status": "harness_error",
                "error_message": f"{type(exc).__name__}: {exc}",
                "final_text": "",
                "usage": {},
                "events": [],
                "final_metadata": {},
            }
        record["case"] = {
            "id": case.get("id"),
            "category": case.get("category"),
            "difficulty": case.get("difficulty"),
        }
        record["evaluation"] = score_case(case, record)
        records.append(record)
        print(json.dumps({"case_id": case["id"], "repetition": repetition, "status": record.get("status"), **record["evaluation"]}, ensure_ascii=False), flush=True)

    report = {
        "schema": "subjects_agent_eval/v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "manifest": {
            "suite_id": suite.get("suite_id"),
            "suite_sha256": hashlib.sha256(raw_suite).hexdigest(),
            "target": args.target,
            "base_url": args.base_url,
            "repetitions": args.repetitions,
            "seed": args.seed,
        },
        "metrics": aggregate(records, input_price=args.input_price_per_1k, output_price=args.output_price_per_1k),
        "records": records,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "metrics": report["metrics"]}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
