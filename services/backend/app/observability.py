from __future__ import annotations

import re
import threading
import time
import uuid
from collections import defaultdict
from contextvars import ContextVar
from typing import Any

from fastapi import Request, Response


trace_id_context: ContextVar[str] = ContextVar("trace_id", default="")
_lock = threading.Lock()
_request_counts: dict[tuple[str, str, int], int] = defaultdict(int)
_request_duration_ms: dict[tuple[str, str], list[float]] = defaultdict(list)
_JOB_ID_RE = re.compile(r"/chat_jobs/[^/]+")
_CHUNK_ID_RE = re.compile(r"/chunks/[^/]+")


async def trace_metrics_middleware(request: Request, call_next: Any) -> Response:
    trace_id = request.headers.get("X-Trace-ID", "").strip()[:64] or uuid.uuid4().hex
    token = trace_id_context.set(trace_id)
    started = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        response.headers["X-Trace-ID"] = trace_id
        return response
    finally:
        elapsed_ms = (time.perf_counter() - started) * 1000
        route = _normalized_path(request.url.path)
        with _lock:
            _request_counts[(request.method, route, status)] += 1
            samples = _request_duration_ms[(request.method, route)]
            samples.append(elapsed_ms)
            if len(samples) > 2000:
                del samples[:-1000]
        trace_id_context.reset(token)


def current_trace_id() -> str:
    return trace_id_context.get()


def metrics_snapshot() -> dict[str, Any]:
    with _lock:
        counters = [
            {"method": method, "route": route, "status": status, "count": count}
            for (method, route, status), count in sorted(_request_counts.items())
        ]
        latency = []
        for (method, route), values in sorted(_request_duration_ms.items()):
            ordered = sorted(values)
            latency.append({
                "method": method,
                "route": route,
                "count": len(ordered),
                "p50_ms": _percentile(ordered, 0.50),
                "p95_ms": _percentile(ordered, 0.95),
                "p99_ms": _percentile(ordered, 0.99),
            })
    return {"requests": counters, "latency": latency}


def _normalized_path(path: str) -> str:
    return _CHUNK_ID_RE.sub("/chunks/{chunk_id}", _JOB_ID_RE.sub("/chat_jobs/{job_id}", path))


def _percentile(values: list[float], ratio: float) -> float:
    if not values:
        return 0.0
    index = min(len(values) - 1, max(0, int((len(values) - 1) * ratio)))
    return round(values[index], 3)
