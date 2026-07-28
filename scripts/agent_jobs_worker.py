from __future__ import annotations

import asyncio
import contextlib
import os
import signal


async def main() -> None:
    os.environ.setdefault("AGENT_JOB_ROLE", "worker")
    if os.getenv("AGENT_JOB_BROKER", "auto").strip().lower() in {"auto", "memory"}:
        if not os.getenv("REDIS_URL"):
            raise RuntimeError("agent worker requires REDIS_URL and AGENT_JOB_BROKER=redis")
        os.environ["AGENT_JOB_BROKER"] = "redis"

    from backend.app.services.agent_jobs import get_agent_job_service

    service = get_agent_job_service()
    await service.start()

    stop_event = asyncio.Event()

    def _stop(*_: object) -> None:
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, _stop)

    try:
        await stop_event.wait()
    finally:
        await service.stop()


if __name__ == "__main__":
    asyncio.run(main())
