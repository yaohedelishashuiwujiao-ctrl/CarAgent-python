from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request


def main() -> None:
    port = int(os.getenv("AGENT_JOB_SMOKE_PORT", "8099"))
    env = {
        **os.environ,
        "APP_ENV": "local",
        "DATA_BACKEND": "memory",
        "ALLOW_INSECURE_DEV_AUTH": "true",
        "AGENT_JOB_EXECUTOR": "mock",
        "AGENT_JOB_ROLE": "all",
        "AGENT_JOB_BROKER": "memory",
        "AGENT_WORKER_CONCURRENCY": "4",
        "AGENT_MODEL_CONCURRENCY_ARK": "4",
        "AGENT_SCHEDULER_TICK_MS": "1",
    }
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "backend.app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_for_health(port)
        job_id = _create_job(port, "小米SU7轴距", "http-smoke", "u1", "tenant-smoke", "smoke-key")
        duplicate_id = _create_job(port, "这段内容不得覆盖原任务", "http-smoke", "u1", "tenant-smoke", "smoke-key")
        if duplicate_id != job_id:
            raise RuntimeError("idempotency key created more than one job")
        job = _wait_for_job(port, job_id, "u1", "tenant-smoke")
        body = _read_events(port, job_id, "u1", "tenant-smoke")
        print(f"job_id={job_id}")
        print(f"status={job['status']}")
        print(f"final_text={job['final_text'][:40]}")
        print(f"events_preview={body[:240].replace(chr(10), ' ')}")
        if job["status"] != "succeeded" or job["user_id"] != "u1" or job["tenant_id"] != "tenant-smoke" or "event: final" not in body:
            raise SystemExit(1)
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


def _wait_for_health(port: int) -> None:
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1) as response:
                if response.status == 200:
                    return
        except Exception:
            time.sleep(0.1)
    raise RuntimeError("smoke server did not become healthy")


def _create_job(port: int, prompt: str, session_id: str, user_id: str, tenant_id: str, idempotency_key: str) -> str:
    data = json.dumps({"prompt": prompt, "session_id": session_id, "user_id": "forged-body-user", "tenant_id": "forged-body-tenant"}).encode()
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/agent/chat_jobs",
        data=data,
        headers={
            "Content-Type": "application/json",
            "X-User-ID": user_id,
            "X-Tenant-ID": tenant_id,
            "Idempotency-Key": idempotency_key,
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return str(json.loads(response.read())["job_id"])


def _wait_for_job(port: int, job_id: str, user_id: str, tenant_id: str) -> dict:
    deadline = time.time() + 10
    while time.time() < deadline:
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/agent/chat_jobs/{job_id}",
            headers={"X-User-ID": user_id, "X-Tenant-ID": tenant_id},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            job = json.loads(response.read())
        if job["status"] == "succeeded":
            return job
        time.sleep(0.05)
    raise RuntimeError("job did not finish")


def _read_events(port: int, job_id: str, user_id: str, tenant_id: str) -> str:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/agent/chat_jobs/{job_id}/events?after_seq=0",
        headers={"X-User-ID": user_id, "X-Tenant-ID": tenant_id},
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return response.read().decode("utf-8")


if __name__ == "__main__":
    main()
