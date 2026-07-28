from __future__ import annotations

import argparse
import asyncio
import os
import sys
import statistics
import time
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


async def main() -> None:
    parser = argparse.ArgumentParser(description="Load test the in-process Agent job scheduler.")
    parser.add_argument("--jobs", type=int, default=2000)
    parser.add_argument("--users", type=int, default=2000)
    parser.add_argument("--workers", type=int, default=200)
    parser.add_argument("--model-concurrency", type=int, default=200)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--broker", choices=("memory", "redis"), default="memory")
    args = parser.parse_args()

    os.environ["AGENT_JOB_EXECUTOR"] = "mock"
    os.environ["AGENT_JOB_ROLE"] = "all"
    os.environ["DATA_BACKEND"] = "memory"
    os.environ["AGENT_JOB_BROKER"] = args.broker
    if args.broker == "redis":
        namespace = f"agent:jobs:loadtest:{uuid.uuid4().hex}"
        os.environ["AGENT_JOB_REDIS_STREAM"] = namespace
        os.environ["AGENT_JOB_REDIS_GROUP"] = "loadtest-workers"
        os.environ["AGENT_SESSION_LOCK_PREFIX"] = f"{namespace}:session:"
        os.environ["AGENT_SCHEDULER_LEADER_KEY"] = f"{namespace}:leader"
    os.environ["AGENT_WORKER_CONCURRENCY"] = str(args.workers)
    os.environ["AGENT_MODEL_CONCURRENCY_ARK"] = str(args.model_concurrency)
    os.environ["AGENT_SQL_CONCURRENCY"] = str(max(args.model_concurrency, 200))
    os.environ["AGENT_TOOL_CONCURRENCY"] = str(max(args.model_concurrency, 200))
    os.environ["AGENT_MAX_PENDING_JOBS"] = str(max(args.jobs * 10, 20000))
    os.environ["AGENT_MAX_PENDING_PER_USER"] = str(max(20, args.jobs))
    os.environ["AGENT_SCHEDULER_TICK_MS"] = "1"

    from backend.app.services.agent_jobs import AgentJobService, JobStatus

    service = AgentJobService()
    await service.start()
    created_ids: list[str] = []
    tenant_id = f"loadtest-{uuid.uuid4().hex[:8]}"
    started = time.perf_counter()

    async def submit(index: int) -> None:
        job = await service.create_job(
            prompt=f"高并发压测问题 {index}",
            session_id=f"session-{index}",
            user_id=f"user-{index % args.users}",
            tenant_id=tenant_id,
            max_turns=2,
        )
        created_ids.append(job.id)

    await asyncio.gather(*(submit(index) for index in range(args.jobs)))
    submit_elapsed = time.perf_counter() - started

    deadline = time.perf_counter() + args.timeout
    while time.perf_counter() < deadline:
        statuses_by_id = await service.snapshot_job_statuses(created_ids)
        finished_count = sum(
            1
            for status in statuses_by_id.values()
            if status in {
                JobStatus.SUCCEEDED.value,
                JobStatus.FAILED.value,
                JobStatus.CANCELLED.value,
                JobStatus.REJECTED.value,
            }
        )
        if finished_count >= args.jobs:
            break
        await asyncio.sleep(0.1)

    finished = time.perf_counter()
    durations: list[float] = []
    statuses: dict[str, int] = {}
    for job_id in created_ids:
        job = await service.get_job(job_id)
        if not job:
            continue
        statuses[job.status.value] = statuses.get(job.status.value, 0) + 1
        if job.finished_at:
            durations.append(job.finished_at - job.created_at)

    await service.stop()

    total_elapsed = finished - started
    p50 = statistics.quantiles(durations, n=100)[49] if len(durations) >= 100 else (max(durations) if durations else 0)
    p95 = statistics.quantiles(durations, n=100)[94] if len(durations) >= 100 else (max(durations) if durations else 0)
    p99 = statistics.quantiles(durations, n=100)[98] if len(durations) >= 100 else (max(durations) if durations else 0)
    succeeded = statuses.get(JobStatus.SUCCEEDED.value, 0)
    failed = statuses.get(JobStatus.FAILED.value, 0)
    throughput = succeeded / total_elapsed if total_elapsed > 0 else 0

    print(f"jobs_submitted={args.jobs}")
    print(f"broker={args.broker}")
    print(f"jobs_created={len(created_ids)}")
    print(f"statuses={statuses}")
    print(f"submit_elapsed_seconds={submit_elapsed:.3f}")
    print(f"total_elapsed_seconds={total_elapsed:.3f}")
    print(f"throughput_jobs_per_second={throughput:.1f}")
    print(f"latency_p50_seconds={p50:.3f}")
    print(f"latency_p95_seconds={p95:.3f}")
    print(f"latency_p99_seconds={p99:.3f}")
    print(f"failed={failed}")
    if succeeded != args.jobs or failed:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
