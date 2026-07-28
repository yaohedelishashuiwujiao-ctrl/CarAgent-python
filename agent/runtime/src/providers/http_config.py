from __future__ import annotations

import os
from typing import Any


def openai_http_options() -> dict[str, Any]:
    """Bound model requests below the outer Backend proxy timeout."""
    timeout_s = _bounded_float("CLAWD_MODEL_REQUEST_TIMEOUT_SECONDS", 90.0, 10.0, 210.0)
    connect_s = min(10.0, timeout_s)
    max_retries = _bounded_int("CLAWD_MODEL_HTTP_MAX_RETRIES", 1, 0, 2)
    options: dict[str, Any] = {"max_retries": max_retries}
    try:
        import httpx

        options["http_client"] = httpx.Client(
            trust_env=False,
            timeout=httpx.Timeout(timeout_s, connect=connect_s),
        )
    except Exception:
        options["timeout"] = timeout_s
    return options


def _bounded_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(value, maximum))


def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(value, maximum))
