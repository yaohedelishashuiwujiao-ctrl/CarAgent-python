from __future__ import annotations

import argparse
import asyncio
import json
import math
import random
import statistics
import time
import uuid
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx


PROMPTS: tuple[tuple[str, str], ...] = (
    ("simple", "小米SU7的轴距是多少？请使用平台已有数据简洁回答。"),
    ("simple", "解释一下汽车前双叉臂悬架的主要优缺点。"),
    ("single_tool", "查询平台数据中小米SU7的前后悬架形式，并给出数据来源。"),
    ("single_tool", "从已有车型数据中查找轴距超过3000毫米的车型，列出前5个。"),
    ("multi_tool", "基于平台已有数据，对比小米SU7和智界S7的轴距、悬架和制动配置。"),
    ("multi_tool", "分析平台内中大型纯电轿车的底盘配置差异，给出有依据的结论。"),
    ("analysis", "结合已有车型数据和文档，写一份小米SU7底盘竞品分析摘要。"),
)

CATEGORY_WEIGHTS = {
    "simple": 0.30,
    "single_tool": 0.35,
    "multi_tool": 0.25,
    "analysis": 0.10,
}


@dataclass(frozen=True)
class Arrival:
    offset_seconds: float
    user_index: int
    sequence: int
    category: str
    prompt: str


@dataclass
class RequestResult:
    session_id: str
    user_index: int
    sequence: int
    category: str
    scheduled_at_seconds: float
    started_at_seconds: float
    status_code: int | None = None
    job_id: str | None = None
    error: str | None = None
    total_seconds: float = 0.0
    first_event_seconds: float | None = None
    first_text_seconds: float | None = None
    queue_wait_seconds: float | None = None
    num_turns: int | None = None
    tool_uses: int = 0
    tool_results: int = 0
    tool_errors: int = 0
    tool_names: list[str] = field(default_factory=list)
    final_received: bool = False
    max_turns_reached: bool = False
    response_chars: int = 0
    model: str | None = None


class ConcurrencyGauge:
    def __init__(self) -> None:
        self.current = 0
        self.peak = 0
        self._lock = asyncio.Lock()

    async def enter(self) -> None:
        async with self._lock:
            self.current += 1
            self.peak = max(self.peak, self.current)

    async def leave(self) -> None:
        async with self._lock:
            self.current -= 1


def percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(quantile * len(ordered)) - 1))
    return round(ordered[index], 3)


def choose_prompt(rng: random.Random) -> tuple[str, str]:
    category = rng.choices(
        list(CATEGORY_WEIGHTS),
        weights=list(CATEGORY_WEIGHTS.values()),
        k=1,
    )[0]
    candidates = [item for item in PROMPTS if item[0] == category]
    return rng.choice(candidates)


def generate_arrivals(
    *,
    users: int,
    duration_seconds: float,
    mean_question_interval_seconds: float,
    seed: int,
) -> list[Arrival]:
    rng = random.Random(seed)
    arrivals: list[Arrival] = []
    rate = 1.0 / mean_question_interval_seconds
    for user_index in range(users):
        offset = rng.expovariate(rate)
        sequence = 0
        while offset < duration_seconds:
            sequence += 1
            category, prompt = choose_prompt(rng)
            arrivals.append(Arrival(offset, user_index, sequence, category, prompt))
            offset += rng.expovariate(rate)
    arrivals.sort(key=lambda item: item.offset_seconds)
    return arrivals


def parse_event(result: RequestResult, event: dict[str, Any], elapsed: float) -> None:
    if result.first_event_seconds is None:
        result.first_event_seconds = elapsed
    event_type = event.get("type")
    if event_type in {"admitted", "running"} and result.queue_wait_seconds is None:
        result.queue_wait_seconds = elapsed
    elif event_type == "text_delta" and result.first_text_seconds is None:
        result.first_text_seconds = elapsed
    elif event_type == "tool_use":
        result.tool_uses += 1
        result.tool_names.append(str(event.get("tool") or "unknown"))
    elif event_type == "tool_result":
        result.tool_results += 1
        if event.get("status") == "error":
            result.tool_errors += 1
    elif event_type == "tool_error":
        result.tool_errors += 1
    elif event_type == "error":
        result.error = str(event.get("error") or "Agent stream error")
    elif event_type == "final":
        result.final_received = True
        result.num_turns = int(event["num_turns"]) if event.get("num_turns") is not None else None
        result.max_turns_reached = bool(event.get("max_turns_reached"))
        result.response_chars = len(str(event.get("text") or ""))
        result.model = str(event.get("model")) if event.get("model") else None


def parse_sse_frame(frame: str) -> dict[str, Any] | None:
    lines = frame.splitlines()
    event_name = next((line[6:].strip() for line in lines if line.startswith("event:")), "message")
    data = "\n".join(line[5:].lstrip() for line in lines if line.startswith("data:"))
    if not data:
        return None
    envelope = json.loads(data)
    payload = envelope.get("payload") if isinstance(envelope.get("payload"), dict) else {}
    return {"type": str(envelope.get("event_type") or event_name), **payload}


async def run_arrival(
    *,
    arrival: Arrival,
    test_started: float,
    endpoint: str,
    client: httpx.AsyncClient,
    gauge: ConcurrencyGauge,
    hard_timeout_seconds: float,
) -> RequestResult:
    delay = test_started + arrival.offset_seconds - time.perf_counter()
    if delay > 0:
        await asyncio.sleep(delay)

    started = time.perf_counter()
    session_id = f"load-{arrival.user_index}-{uuid.uuid4().hex[:10]}"
    result = RequestResult(
        session_id=session_id,
        user_index=arrival.user_index,
        sequence=arrival.sequence,
        category=arrival.category,
        scheduled_at_seconds=round(arrival.offset_seconds, 3),
        started_at_seconds=round(started - test_started, 3),
    )
    create_payload = {
        "session_id": session_id,
        "prompt": arrival.prompt,
        "user_id": f"load-user-{arrival.user_index}",
        "tenant_id": "load-test",
        "idempotency_key": uuid.uuid4().hex,
    }

    await gauge.enter()
    try:
        async with asyncio.timeout(hard_timeout_seconds):
            create_response = await client.post(endpoint, json=create_payload)
            result.status_code = create_response.status_code
            if create_response.status_code != 202:
                try:
                    detail = create_response.json()
                    result.error = str(detail.get("error") or detail.get("detail") or create_response.text)
                except json.JSONDecodeError:
                    result.error = create_response.text[:500]
                return result
            created = create_response.json()
            result.job_id = str(created["job_id"])
            events_url = str(created["events_url"])
            if events_url.startswith("/"):
                base_url = endpoint.split("/api/", 1)[0]
                events_url = f"{base_url}{events_url}"

            async with client.stream("GET", events_url) as response:
                response.raise_for_status()
                buffer = ""
                async for chunk in response.aiter_text():
                    buffer += chunk
                    frames = buffer.replace("\r\n", "\n").split("\n\n")
                    buffer = frames.pop()
                    for frame in frames:
                        try:
                            event = parse_sse_frame(frame)
                        except json.JSONDecodeError as exc:
                            result.error = f"Invalid SSE event: {exc}"
                            continue
                        if event is None:
                            continue
                        parse_event(result, event, time.perf_counter() - started)
                        if result.final_received:
                            break
                    if result.final_received:
                        break
                if buffer.strip():
                    try:
                        event = parse_sse_frame(buffer)
                        if event is not None:
                            parse_event(result, event, time.perf_counter() - started)
                    except json.JSONDecodeError as exc:
                        result.error = f"Invalid trailing SSE event: {exc}"
    except TimeoutError:
        result.error = f"HardTimeout: request exceeded {hard_timeout_seconds:.1f}s"
        if result.job_id:
            try:
                await client.post(
                    f"{endpoint}/{result.job_id}/cancel",
                    timeout=5.0,
                )
            except Exception:
                pass
    except Exception as exc:
        result.error = f"{type(exc).__name__}: {exc}"
    finally:
        result.total_seconds = round(time.perf_counter() - started, 3)
        await gauge.leave()
    return result


def build_summary(
    *,
    args: argparse.Namespace,
    arrivals: list[Arrival],
    results: list[RequestResult],
    elapsed_seconds: float,
    peak_concurrency: int,
    runtime_status: dict[str, Any],
) -> dict[str, Any]:
    completed = [item for item in results if item.status_code == 202 and item.final_received and not item.error]
    accepted = [item for item in results if item.status_code == 202]
    latencies = [item.total_seconds for item in completed]
    first_text = [item.first_text_seconds for item in completed if item.first_text_seconds is not None]
    queue_waits = [item.queue_wait_seconds for item in completed if item.queue_wait_seconds is not None]
    turns = [item.num_turns for item in completed if item.num_turns is not None]
    status_counts = Counter(str(item.status_code or "transport_error") for item in results)
    error_counts = Counter(item.error or "none" for item in results if item.error)
    category_counts = Counter(item.category for item in results)
    tool_name_counts = Counter(name for item in completed for name in item.tool_names)
    users_with_questions = len({item.user_index for item in arrivals})

    return {
        "test": {
            "base_url": args.base_url,
            "endpoint": "/api/agent/chat_jobs",
            "online_users": args.users,
            "duration_seconds": args.duration,
            "mean_question_interval_seconds": args.mean_question_interval,
            "per_user_question_probability": round(1 - math.exp(-args.duration / args.mean_question_interval), 6),
            "seed": args.seed,
            "generated_questions": len(arrivals),
            "users_with_questions": users_with_questions,
            "question_categories": dict(category_counts),
            "elapsed_seconds": round(elapsed_seconds, 3),
        },
        "runtime_before_test": runtime_status,
        "result": {
            "status_counts": dict(status_counts),
            "accepted": len(accepted),
            "completed": len(completed),
            "rejected_or_failed": len(results) - len(completed),
            "completion_rate": round(len(completed) / len(results), 4) if results else 0.0,
            "throughput_completed_per_second": round(len(completed) / elapsed_seconds, 3) if elapsed_seconds else 0.0,
            "client_peak_in_flight": peak_concurrency,
            "latency_seconds": {
                "p50": percentile(latencies, 0.50),
                "p95": percentile(latencies, 0.95),
                "p99": percentile(latencies, 0.99),
                "max": round(max(latencies), 3) if latencies else None,
            },
            "first_text_seconds": {
                "p50": percentile(first_text, 0.50),
                "p95": percentile(first_text, 0.95),
            },
            "queue_wait_seconds": {
                "p50": percentile(queue_waits, 0.50),
                "p95": percentile(queue_waits, 0.95),
                "max": round(max(queue_waits), 3) if queue_waits else None,
            },
            "agent_turns": {
                "count": len(turns),
                "mean": round(statistics.mean(turns), 3) if turns else None,
                "p50": percentile([float(item) for item in turns], 0.50),
                "p95": percentile([float(item) for item in turns], 0.95),
                "max": max(turns) if turns else None,
                "total": sum(turns),
            },
            "tool_calls": {
                "uses": sum(item.tool_uses for item in completed),
                "results": sum(item.tool_results for item in completed),
                "errors": sum(item.tool_errors for item in completed),
                "mean_uses_per_completed_request": round(
                    sum(item.tool_uses for item in completed) / len(completed), 3
                )
                if completed
                else None,
                "by_name": dict(tool_name_counts.most_common()),
            },
            "max_turns_reached": sum(item.max_turns_reached for item in completed),
            "top_errors": error_counts.most_common(10),
        },
        "requests": [asdict(item) for item in results],
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description="Probability-based load test through the real frontend Agent API.")
    parser.add_argument("--base-url", default="http://127.0.0.1:5173")
    parser.add_argument("--users", type=int, default=2000)
    parser.add_argument("--duration", type=float, default=60.0)
    parser.add_argument("--mean-question-interval", type=float, default=600.0)
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--max-connections", type=int, default=300)
    parser.add_argument("--seed", type=int, default=20260713)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    if args.users <= 0 or args.duration <= 0 or args.mean_question_interval <= 0:
        parser.error("users, duration, and mean-question-interval must be positive")

    base_url = args.base_url.rstrip("/")
    endpoint = f"{base_url}/api/agent/chat_jobs"
    timeout = httpx.Timeout(args.timeout, connect=10.0)
    limits = httpx.Limits(max_connections=args.max_connections, max_keepalive_connections=args.max_connections)
    arrivals = generate_arrivals(
        users=args.users,
        duration_seconds=args.duration,
        mean_question_interval_seconds=args.mean_question_interval,
        seed=args.seed,
    )

    async with httpx.AsyncClient(timeout=timeout, limits=limits, trust_env=False) as client:
        status_response = await client.get(f"{base_url}/api/agent/status")
        status_response.raise_for_status()
        status = status_response.json()
        gauge = ConcurrencyGauge()
        started = time.perf_counter()
        tasks = [
            asyncio.create_task(
                run_arrival(
                    arrival=arrival,
                    test_started=started,
                    endpoint=endpoint,
                    client=client,
                    gauge=gauge,
                    hard_timeout_seconds=args.timeout,
                )
            )
            for arrival in arrivals
        ]
        results = await asyncio.gather(*tasks) if tasks else []
        elapsed = time.perf_counter() - started

    report = build_summary(
        args=args,
        arrivals=arrivals,
        results=results,
        elapsed_seconds=elapsed,
        peak_concurrency=gauge.peak,
        runtime_status=status.get("agent_runtime") or {},
    )
    output = args.output or Path(
        f"/tmp/agent_frontend_load_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({"report": str(output), **report["test"], **report["result"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
