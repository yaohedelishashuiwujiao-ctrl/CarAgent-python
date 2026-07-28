from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Any, Protocol

from backend.app.config import settings


@dataclass(frozen=True)
class DispatchMessage:
    message_id: str
    job_id: str
    execution_token: str | None = None
    queue_key: str | None = None


class AgentJobDispatchBackend(Protocol):
    async def enqueue(
        self,
        job_id: str,
        *,
        queue_key: str | None = None,
        execution_token: str | None = None,
    ) -> None:
        ...

    async def get(self, consumer_id: str, *, block_ms: int = 1000) -> DispatchMessage | None:
        ...

    async def ack(self, message: DispatchMessage) -> None:
        ...

    async def backlog(self) -> int:
        ...

    async def close(self) -> None:
        ...


class MemoryAgentJobDispatchBackend:
    def __init__(self) -> None:
        self._queue: asyncio.Queue[DispatchMessage] = asyncio.Queue()

    async def enqueue(
        self,
        job_id: str,
        *,
        queue_key: str | None = None,
        execution_token: str | None = None,
    ) -> None:
        await self._queue.put(
            DispatchMessage(message_id=job_id, job_id=job_id, execution_token=execution_token, queue_key=queue_key)
        )

    async def get(self, consumer_id: str, *, block_ms: int = 1000) -> DispatchMessage | None:
        try:
            return await asyncio.wait_for(self._queue.get(), timeout=max(block_ms, 1) / 1000)
        except asyncio.TimeoutError:
            return None

    async def ack(self, message: DispatchMessage) -> None:
        return None

    async def backlog(self) -> int:
        return self._queue.qsize()

    async def close(self) -> None:
        return None


class RedisStreamAgentJobDispatchBackend:
    def __init__(
        self,
        *,
        stream_key: str = "agent:jobs:dispatch",
        group_name: str = "agent-workers",
    ) -> None:
        if not settings.redis_url:
            raise RuntimeError("REDIS_URL is required for RedisAgentJobDispatchBackend")
        self.stream_key = stream_key
        self.group_name = group_name
        self._client = None
        self._group_ready = False
        self._group_lock = asyncio.Lock()

    def _client_async(self):
        if self._client is None:
            import redis.asyncio as redis

            self._client = redis.Redis.from_url(
                settings.redis_url,
                socket_connect_timeout=1.0,
                # XREADGROUP blocks for up to one second. A two-second socket
                # timeout proved too tight under CPU and connection-pool
                # contention and produced false read timeouts in healthy Redis.
                socket_timeout=5.0,
                socket_keepalive=True,
                health_check_interval=30,
                decode_responses=True,
            )
        return self._client

    async def _ensure_group(self) -> None:
        if self._group_ready:
            return
        async with self._group_lock:
            if self._group_ready:
                return
            client = self._client_async()
            try:
                await client.xgroup_create(name=self.stream_key, groupname=self.group_name, id="0-0", mkstream=True)
            except Exception as exc:
                if "BUSYGROUP" not in str(exc):
                    raise
            self._group_ready = True

    async def enqueue(
        self,
        job_id: str,
        *,
        queue_key: str | None = None,
        execution_token: str | None = None,
    ) -> None:
        client = self._client_async()
        fields: dict[str, Any] = {"job_id": job_id}
        if queue_key:
            fields["queue_key"] = queue_key
        if execution_token:
            fields["execution_token"] = execution_token
        await client.xadd(self.stream_key, fields, maxlen=100000, approximate=True)

    async def get(self, consumer_id: str, *, block_ms: int = 1000) -> DispatchMessage | None:
        await self._ensure_group()
        client = self._client_async()
        # Prefer newly dispatched work. Reclaiming abandoned pending entries first can
        # starve fresh requests after a worker restart with a large stale PEL.
        messages = await client.xreadgroup(
            groupname=self.group_name,
            consumername=consumer_id,
            streams={self.stream_key: ">"},
            count=1,
            block=max(block_ms, 1),
        )
        if messages:
            _stream_name, entries = messages[0]
            if entries:
                message_id, payload = entries[0]
                return self._decode_message(message_id, payload)
        # SQL leases are the source of truth for crash recovery and enqueue a
        # fresh token. XAUTOCLAIM cannot distinguish a dead worker from a valid
        # long-running agent, so reclaiming by idle time creates duplicate runs.
        return None

    def _decode_message(self, message_id: Any, payload: dict[str, Any]) -> DispatchMessage:
        job_id = payload.get("job_id") or payload.get(b"job_id")
        if isinstance(job_id, bytes):
            job_id = job_id.decode("utf-8", errors="ignore")
        queue_key = payload.get("queue_key") or payload.get(b"queue_key")
        if isinstance(queue_key, bytes):
            queue_key = queue_key.decode("utf-8", errors="ignore")
        execution_token = payload.get("execution_token") or payload.get(b"execution_token")
        if isinstance(execution_token, bytes):
            execution_token = execution_token.decode("utf-8", errors="ignore")
        return DispatchMessage(
            message_id=message_id.decode("utf-8") if isinstance(message_id, bytes) else str(message_id),
            job_id=str(job_id or ""),
            execution_token=str(execution_token) if execution_token else None,
            queue_key=str(queue_key) if queue_key else None,
        )

    async def ack(self, message: DispatchMessage) -> None:
        client = self._client_async()
        await client.xack(self.stream_key, self.group_name, message.message_id)

    async def backlog(self) -> int:
        client = self._client_async()
        try:
            groups = await client.xinfo_groups(self.stream_key)
        except Exception:
            return 0
        for group in groups or []:
            if group.get("name") == self.group_name:
                return int(group.get("pending") or 0) + int(group.get("lag") or 0)
        return 0

    async def close(self) -> None:
        if self._client is None:
            return
        await self._client.aclose()


def build_agent_job_dispatch_backend() -> AgentJobDispatchBackend:
    broker = (settings.agent_job_broker or "auto").strip().lower()
    if broker == "auto":
        broker = "redis" if settings.redis_url else "memory"
    if broker == "redis":
        if not settings.redis_url:
            raise RuntimeError("AGENT_JOB_BROKER=redis requires REDIS_URL")
        return RedisStreamAgentJobDispatchBackend(
            stream_key=os.getenv("AGENT_JOB_REDIS_STREAM", "agent:jobs:dispatch"),
            group_name=os.getenv("AGENT_JOB_REDIS_GROUP", "agent-workers"),
        )
    if broker == "memory":
        return MemoryAgentJobDispatchBackend()
    raise RuntimeError(f"unsupported AGENT_JOB_BROKER value: {settings.agent_job_broker}")
