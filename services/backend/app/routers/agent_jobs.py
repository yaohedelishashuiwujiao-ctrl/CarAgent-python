from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import StreamingResponse

from backend.app.services.agent_jobs import get_agent_job_service
from backend.app.services.agent_jobs_types import JobStatus
from backend.app.security import Principal, get_request_principal
from backend.app.observability import current_trace_id


router = APIRouter()


@router.post("/chat_jobs", status_code=202)
async def create_chat_job(
    payload: dict[str, Any],
    principal: Principal = Depends(get_request_principal),
    idempotency_header: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    prompt = str(payload.get("prompt") or "").strip()
    session_id = str(payload.get("session_id") or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="prompt is required")
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id is required")
    try:
        service = get_agent_job_service()
        job = await service.create_job(
            prompt=prompt,
            session_id=session_id,
            user_id=principal.user_id,
            tenant_id=principal.tenant_id,
            max_turns=int(payload["max_turns"]) if payload.get("max_turns") is not None else None,
            idempotency_key=(idempotency_header or str(payload.get("idempotency_key") or "")) or None,
            role_ids=principal.role_ids,
            data_scope=principal.data_scope,
            allowed_tools=principal.allowed_tools,
            trace_id=current_trace_id(),
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "job_id": job.id,
        "status": job.status.value,
        "queue_key": job.queue_key,
        "estimated_cost": job.estimated_cost,
        "status_url": f"/api/agent/chat_jobs/{job.id}",
        "events_url": f"/api/agent/chat_jobs/{job.id}/events",
    }


@router.get("/chat_jobs/{job_id}")
async def get_chat_job(job_id: str, principal: Principal = Depends(get_request_principal)) -> dict[str, Any]:
    service = get_agent_job_service()
    job = await service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    _ensure_job_access(principal, job.tenant_id, job.user_id)
    return service.serialize_job(job)


@router.get("/chat_jobs/{job_id}/events")
async def stream_chat_job_events(
    job_id: str,
    after_seq: int = Query(0, ge=0),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    principal: Principal = Depends(get_request_principal),
) -> StreamingResponse:
    service = get_agent_job_service()
    job = await service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    _ensure_job_access(principal, job.tenant_id, job.user_id)
    if last_event_id:
        try:
            after_seq = max(after_seq, int(last_event_id))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Last-Event-ID must be an integer") from exc

    async def iterator() -> AsyncIterator[bytes]:
        queue = await service.subscribe(job_id)
        last_seq = after_seq
        try:
            for event in await service.get_events_after(job_id, after_seq):
                yield _sse_event(event.event_type, event.seq, service.serialize_event(event))
                last_seq = max(last_seq, event.seq)
                if event.event_type in {"final", "error", "cancelled"}:
                    return
            job = await service.get_job(job_id)
            if job and job.status in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.REJECTED}:
                return
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    recent_events = await service.get_events_after(job_id, last_seq)
                    if recent_events:
                        for event in recent_events:
                            yield _sse_event(event.event_type, event.seq, service.serialize_event(event))
                            last_seq = max(last_seq, event.seq)
                            if event.event_type in {"final", "error", "cancelled"}:
                                return
                        continue
                    yield b": keepalive\n\n"
                    continue
                yield _sse_event(event.event_type, event.seq, service.serialize_event(event))
                last_seq = max(last_seq, event.seq)
                if event.event_type in {"final", "error", "cancelled"}:
                    return
        finally:
            await service.unsubscribe(job_id, queue)

    return StreamingResponse(iterator(), media_type="text/event-stream")


@router.post("/chat_jobs/{job_id}/cancel")
async def cancel_chat_job(job_id: str, principal: Principal = Depends(get_request_principal)) -> dict[str, Any]:
    service = get_agent_job_service()
    existing = await service.get_job(job_id)
    if not existing:
        raise HTTPException(status_code=404, detail="job not found")
    _ensure_job_access(principal, existing.tenant_id, existing.user_id)
    job = await service.cancel_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return service.serialize_job(job)


@router.get("/chat_jobs_runtime/status")
async def chat_jobs_runtime_status() -> dict[str, Any]:
    return await get_agent_job_service().stats()


def _ensure_job_access(principal: Principal, tenant_id: str, user_id: str) -> None:
    if not principal.can_access_job(tenant_id, user_id):
        # Do not disclose whether a cross-tenant job exists.
        raise HTTPException(status_code=404, detail="job not found")


def _sse_event(event_type: str, seq: int, payload: dict[str, Any]) -> bytes:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"id: {seq}\nevent: {event_type}\ndata: {body}\n\n".encode("utf-8")
