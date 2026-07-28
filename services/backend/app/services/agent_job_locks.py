from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from typing import Protocol

from backend.app.config import settings


@dataclass(frozen=True)
class SessionLockLease:
    session_id: str
    owner_id: str
    ttl_ms: int


class AgentSessionLockBackend(Protocol):
    async def acquire(self, session_id: str, owner_id: str, *, ttl_ms: int) -> bool:
        ...

    async def release(self, session_id: str, owner_id: str) -> bool:
        ...

    async def extend(self, session_id: str, owner_id: str, *, ttl_ms: int) -> bool:
        ...

    async def close(self) -> None:
        ...


class AgentSchedulerLeaderBackend(Protocol):
    async def acquire(self, owner_id: str, *, ttl_ms: int) -> bool:
        ...

    async def extend(self, owner_id: str, *, ttl_ms: int) -> bool:
        ...

    async def release(self, owner_id: str) -> bool:
        ...

    async def close(self) -> None:
        ...


class MemoryAgentSessionLockBackend:
    def __init__(self) -> None:
        self._locks: dict[str, tuple[str, float]] = {}
        self._lock = asyncio.Lock()

    async def acquire(self, session_id: str, owner_id: str, *, ttl_ms: int) -> bool:
        now = time.time()
        expires_at = now + max(ttl_ms, 1) / 1000
        async with self._lock:
            current = self._locks.get(session_id)
            if current and current[1] > now and current[0] != owner_id:
                return False
            self._locks[session_id] = (owner_id, expires_at)
            return True

    async def release(self, session_id: str, owner_id: str) -> bool:
        async with self._lock:
            current = self._locks.get(session_id)
            if not current or current[0] != owner_id:
                return False
            self._locks.pop(session_id, None)
            return True

    async def extend(self, session_id: str, owner_id: str, *, ttl_ms: int) -> bool:
        now = time.time()
        expires_at = now + max(ttl_ms, 1) / 1000
        async with self._lock:
            current = self._locks.get(session_id)
            if not current or current[0] != owner_id:
                return False
            self._locks[session_id] = (owner_id, expires_at)
            return True

    async def close(self) -> None:
        return None


class RedisAgentSessionLockBackend:
    def __init__(self, *, prefix: str = "agent:session_lock:") -> None:
        if not settings.redis_url:
            raise RuntimeError("REDIS_URL is required for RedisAgentSessionLockBackend")
        self.prefix = prefix
        self._client = None
        self._lua_release = None
        self._lua_extend = None

    def _client_sync(self):
        if self._client is None:
            import redis

            self._client = redis.Redis.from_url(
                settings.redis_url,
                socket_connect_timeout=1.0,
                socket_timeout=2.0,
                socket_keepalive=True,
                health_check_interval=30,
            )
        return self._client

    def _release_script(self):
        if self._lua_release is None:
            client = self._client_sync()
            self._lua_release = client.register_script(
                """
                local key = KEYS[1]
                local owner = ARGV[1]
                if redis.call("GET", key) == owner then
                    return redis.call("DEL", key)
                end
                return 0
                """
            )
        return self._lua_release

    def _extend_script(self):
        if self._lua_extend is None:
            client = self._client_sync()
            self._lua_extend = client.register_script(
                """
                local key = KEYS[1]
                local owner = ARGV[1]
                local ttl = tonumber(ARGV[2])
                if redis.call("GET", key) == owner then
                    return redis.call("PEXPIRE", key, ttl)
                end
                return 0
                """
            )
        return self._lua_extend

    def _key(self, session_id: str) -> str:
        return f"{self.prefix}{session_id}"

    async def acquire(self, session_id: str, owner_id: str, *, ttl_ms: int) -> bool:
        return await asyncio.to_thread(self._acquire_sync, session_id, owner_id, ttl_ms)

    def _acquire_sync(self, session_id: str, owner_id: str, ttl_ms: int) -> bool:
        client = self._client_sync()
        try:
            return bool(client.set(self._key(session_id), owner_id, nx=True, px=max(ttl_ms, 1)))
        except Exception:
            return False

    async def release(self, session_id: str, owner_id: str) -> bool:
        return await asyncio.to_thread(self._release_sync, session_id, owner_id)

    def _release_sync(self, session_id: str, owner_id: str) -> bool:
        try:
            result = self._release_script()(keys=[self._key(session_id)], args=[owner_id])
            return bool(result)
        except Exception:
            return False

    async def extend(self, session_id: str, owner_id: str, *, ttl_ms: int) -> bool:
        return await asyncio.to_thread(self._extend_sync, session_id, owner_id, ttl_ms)

    def _extend_sync(self, session_id: str, owner_id: str, ttl_ms: int) -> bool:
        try:
            result = self._extend_script()(keys=[self._key(session_id)], args=[owner_id, max(ttl_ms, 1)])
            return bool(result)
        except Exception:
            return False

    async def close(self) -> None:
        if self._client is None:
            return
        await asyncio.to_thread(self._client.close)


class MemoryAgentSchedulerLeaderBackend:
    def __init__(self) -> None:
        self._owner_id: str | None = None
        self._expires_at: float = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self, owner_id: str, *, ttl_ms: int) -> bool:
        import time

        now = time.time()
        async with self._lock:
            if self._owner_id and self._expires_at > now and self._owner_id != owner_id:
                return False
            self._owner_id = owner_id
            self._expires_at = now + max(ttl_ms, 1) / 1000
            return True

    async def extend(self, owner_id: str, *, ttl_ms: int) -> bool:
        import time

        now = time.time()
        async with self._lock:
            if self._owner_id != owner_id:
                return False
            self._expires_at = now + max(ttl_ms, 1) / 1000
            return True

    async def release(self, owner_id: str) -> bool:
        async with self._lock:
            if self._owner_id != owner_id:
                return False
            self._owner_id = None
            self._expires_at = 0.0
            return True

    async def close(self) -> None:
        return None


class RedisAgentSchedulerLeaderBackend:
    def __init__(self, *, key: str = "agent:scheduler:leader") -> None:
        if not settings.redis_url:
            raise RuntimeError("REDIS_URL is required for RedisAgentSchedulerLeaderBackend")
        self.key = key
        self._client = None
        self._lua_release = None
        self._lua_extend = None

    def _client_sync(self):
        if self._client is None:
            import redis

            self._client = redis.Redis.from_url(
                settings.redis_url,
                socket_connect_timeout=1.0,
                socket_timeout=2.0,
                socket_keepalive=True,
                health_check_interval=30,
            )
        return self._client

    def _release_script(self):
        if self._lua_release is None:
            client = self._client_sync()
            self._lua_release = client.register_script(
                """
                local key = KEYS[1]
                local owner = ARGV[1]
                if redis.call("GET", key) == owner then
                    return redis.call("DEL", key)
                end
                return 0
                """
            )
        return self._lua_release

    def _extend_script(self):
        if self._lua_extend is None:
            client = self._client_sync()
            self._lua_extend = client.register_script(
                """
                local key = KEYS[1]
                local owner = ARGV[1]
                local ttl = tonumber(ARGV[2])
                if redis.call("GET", key) == owner then
                    return redis.call("PEXPIRE", key, ttl)
                end
                return 0
                """
            )
        return self._lua_extend

    async def acquire(self, owner_id: str, *, ttl_ms: int) -> bool:
        return await asyncio.to_thread(self._acquire_sync, owner_id, ttl_ms)

    def _acquire_sync(self, owner_id: str, ttl_ms: int) -> bool:
        try:
            return bool(self._client_sync().set(self.key, owner_id, nx=True, px=max(ttl_ms, 1)))
        except Exception:
            return False

    async def extend(self, owner_id: str, *, ttl_ms: int) -> bool:
        return await asyncio.to_thread(self._extend_sync, owner_id, ttl_ms)

    def _extend_sync(self, owner_id: str, ttl_ms: int) -> bool:
        try:
            result = self._extend_script()(keys=[self.key], args=[owner_id, max(ttl_ms, 1)])
            return bool(result)
        except Exception:
            return False

    async def release(self, owner_id: str) -> bool:
        return await asyncio.to_thread(self._release_sync, owner_id)

    def _release_sync(self, owner_id: str) -> bool:
        try:
            result = self._release_script()(keys=[self.key], args=[owner_id])
            return bool(result)
        except Exception:
            return False

    async def close(self) -> None:
        if self._client is None:
            return
        await asyncio.to_thread(self._client.close)


def build_agent_session_lock_backend() -> AgentSessionLockBackend:
    if settings.agent_job_broker == "redis" or (settings.agent_job_broker == "auto" and settings.redis_url):
        return RedisAgentSessionLockBackend(prefix=os.getenv("AGENT_SESSION_LOCK_PREFIX", "agent:session_lock:"))
    return MemoryAgentSessionLockBackend()


def build_agent_scheduler_leader_backend() -> AgentSchedulerLeaderBackend:
    if settings.agent_job_broker == "redis" or (settings.agent_job_broker == "auto" and settings.redis_url):
        return RedisAgentSchedulerLeaderBackend(key=os.getenv("AGENT_SCHEDULER_LEADER_KEY", "agent:scheduler:leader"))
    return MemoryAgentSchedulerLeaderBackend()
