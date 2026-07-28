from __future__ import annotations

import json
from typing import Any

from backend.app.config import settings


def get_json(key: str) -> Any | None:
    if not settings.redis_url:
        return None
    try:
        import redis

        client = redis.Redis.from_url(settings.redis_url, socket_connect_timeout=0.2, socket_timeout=0.2)
        value = client.get(key)
    except Exception:
        return None
    if value is None:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


def set_json(key: str, value: Any, ttl_seconds: int = 60) -> None:
    if not settings.redis_url:
        return
    try:
        import redis

        client = redis.Redis.from_url(settings.redis_url, socket_connect_timeout=0.2, socket_timeout=0.2)
        client.setex(key, ttl_seconds, json.dumps(value, ensure_ascii=False))
    except Exception:
        return
