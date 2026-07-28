from __future__ import annotations

import os
from typing import Any, Iterator

import requests
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse

from backend.app.config import settings
from backend.app.security import (
    Principal,
    get_request_principal,
    scope_session_id,
    session_scope_prefix,
    unscope_session_id,
)


router = APIRouter()


def _agent_base_url() -> str:
    return (
        os.getenv("CLAWD_AGENT_WEB_URL")
        or os.getenv("AGENT_WEB_BASE_URL")
        or "http://127.0.0.1:7863"
    ).rstrip("/")


def _agent_url(path: str) -> str:
    return f"{_agent_base_url()}{path}"


def _proxy_json(method: str, path: str, payload: dict[str, Any] | None = None) -> JSONResponse:
    try:
        response = requests.request(method, _agent_url(path), json=payload, timeout=60)
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Agent service unavailable: {exc}") from exc

    if response.ok:
        return JSONResponse(response.json(), status_code=response.status_code)

    try:
        detail = response.json()
    except ValueError:
        detail = {"detail": response.text}
    raise HTTPException(status_code=response.status_code, detail=detail)


@router.get("/status")
def status(principal: Principal = Depends(get_request_principal)) -> JSONResponse:
    return _proxy_json("GET", "/api/status")


@router.get("/sessions")
def sessions(principal: Principal = Depends(get_request_principal)) -> JSONResponse:
    payload = _request_json("GET", "/api/sessions")
    prefix = session_scope_prefix(principal.tenant_id, principal.user_id)
    items = payload.get("sessions") if isinstance(payload.get("sessions"), list) else []
    visible = []
    for item in items:
        if not isinstance(item, dict) or not str(item.get("session_id") or "").startswith(prefix):
            continue
        visible.append({**item, "session_id": unscope_session_id(str(item["session_id"]))})
    return JSONResponse({**payload, "sessions": visible})


@router.get("/session")
def session(session_id: str, principal: Principal = Depends(get_request_principal)) -> JSONResponse:
    if not session_id.strip():
        raise HTTPException(status_code=400, detail="session_id is required")
    try:
        scoped = scope_session_id(principal.tenant_id, principal.user_id, session_id)
        response = requests.get(_agent_url("/api/session"), params={"session_id": scoped}, timeout=60)
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Agent service unavailable: {exc}") from exc
    if response.ok:
        payload = response.json()
        if isinstance(payload, dict) and payload.get("session_id"):
            payload["session_id"] = unscope_session_id(str(payload["session_id"]))
        return JSONResponse(payload, status_code=response.status_code)
    try:
        detail = response.json()
    except ValueError:
        detail = {"detail": response.text}
    raise HTTPException(status_code=response.status_code, detail=detail)


@router.post("/session/new")
def new_session(payload: dict[str, Any], principal: Principal = Depends(get_request_principal)) -> JSONResponse:
    session_id = str(payload.get("session_id") or "").strip()
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id is required")
    scoped = scope_session_id(principal.tenant_id, principal.user_id, session_id)
    response = _request_json("POST", "/api/session/new", {"session_id": scoped})
    if response.get("session_id"):
        response["session_id"] = unscope_session_id(str(response["session_id"]))
    return JSONResponse(response)


@router.post("/config")
def config(payload: dict[str, Any], principal: Principal = Depends(get_request_principal)) -> JSONResponse:
    if not principal.is_admin:
        raise HTTPException(status_code=403, detail="administrator role is required")
    return _proxy_json("POST", "/api/config", payload)


@router.post("/chat")
def chat(payload: dict[str, Any], principal: Principal = Depends(get_request_principal)) -> JSONResponse:
    _require_direct_chat_enabled()
    scoped_payload = dict(payload)
    scoped_payload["session_id"] = scope_session_id(
        principal.tenant_id, principal.user_id, str(payload.get("session_id") or "default")
    )
    return _proxy_json("POST", "/api/chat", scoped_payload)


@router.post("/chat_stream")
def chat_stream(payload: dict[str, Any], principal: Principal = Depends(get_request_principal)) -> StreamingResponse:
    _require_direct_chat_enabled()
    scoped_payload = dict(payload)
    scoped_payload["session_id"] = scope_session_id(
        principal.tenant_id, principal.user_id, str(payload.get("session_id") or "default")
    )
    try:
        upstream = requests.post(_agent_url("/api/chat_stream"), json=scoped_payload, stream=True, timeout=(10, None))
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Agent service unavailable: {exc}") from exc

    if not upstream.ok:
        try:
            detail = upstream.json()
        except ValueError:
            detail = {"detail": upstream.text}
        raise HTTPException(status_code=upstream.status_code, detail=detail)

    def iterator() -> Iterator[bytes]:
        try:
            for chunk in upstream.iter_content(chunk_size=1):
                if chunk:
                    yield chunk
        finally:
            upstream.close()

    return StreamingResponse(
        iterator(),
        media_type=upstream.headers.get("Content-Type", "application/x-ndjson; charset=utf-8"),
    )


def _request_json(method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        response = requests.request(method, _agent_url(path), json=payload, timeout=60)
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Agent service unavailable: {exc}") from exc
    if not response.ok:
        raise HTTPException(status_code=response.status_code, detail=response.text[:2000])
    data = response.json()
    return data if isinstance(data, dict) else {}


def _require_direct_chat_enabled() -> None:
    if settings.app_env.strip().lower() not in {"local", "dev", "development", "test"}:
        raise HTTPException(status_code=403, detail="direct Agent chat is disabled; submit an asynchronous chat job")
