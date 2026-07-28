from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any

from fastapi import Header, HTTPException

from backend.app.config import settings


RUNTIME_TOKEN_AUDIENCE = "subjects-agent-runtime"


@dataclass(frozen=True)
class Principal:
    tenant_id: str
    user_id: str
    role_ids: tuple[str, ...] = ()
    data_scope: dict[str, Any] | None = None
    allowed_tools: tuple[str, ...] = ()
    auth_method: str = "token"

    @property
    def is_admin(self) -> bool:
        return bool({"admin", "platform_admin"}.intersection(self.role_ids))

    def can_access_job(self, tenant_id: str, user_id: str) -> bool:
        return self.tenant_id == tenant_id and (self.user_id == user_id or self.is_admin)


def get_request_principal(
    authorization: str | None = Header(default=None),
    x_tenant_id: str | None = Header(default=None),
    x_user_id: str | None = Header(default=None),
    x_role_ids: str | None = Header(default=None),
) -> Principal:
    """Resolve identity without ever consulting the JSON request body.

    Production accepts only a signed bearer token. Local development may use
    headers so the existing standalone frontend remains usable.
    """
    if authorization:
        claims = verify_token(authorization, audience=settings.api_token_audience)
        return _principal_from_claims(claims, auth_method="bearer")

    if not _is_development() or not settings.allow_insecure_dev_auth:
        raise HTTPException(status_code=401, detail="signed bearer token is required")

    roles = tuple(part.strip() for part in (x_role_ids or "user").split(",") if part.strip())
    return Principal(
        tenant_id=(x_tenant_id or "default").strip() or "default",
        user_id=(x_user_id or "anonymous").strip() or "anonymous",
        role_ids=roles,
        data_scope={"scope": "published", "source": "local-dev-header"},
        allowed_tools=settings.default_agent_tools,
        auth_method="local-dev-header",
    )


def get_data_principal(
    authorization: str | None = Header(default=None),
    x_tenant_id: str | None = Header(default=None),
    x_user_id: str | None = Header(default=None),
    x_role_ids: str | None = Header(default=None),
) -> Principal:
    """Accept API tokens and short-lived Runtime tokens for data-plane calls."""
    if authorization:
        try:
            claims = verify_token(authorization, audience=settings.api_token_audience)
        except HTTPException:
            claims = verify_token(authorization, audience=RUNTIME_TOKEN_AUDIENCE)
        return _principal_from_claims(claims, auth_method="data-plane-bearer")
    return get_request_principal(None, x_tenant_id, x_user_id, x_role_ids)


def issue_runtime_token(
    *,
    job_id: str,
    session_id: str,
    principal: Principal,
    ttl_seconds: int | None = None,
    trace_id: str | None = None,
) -> str:
    now = int(time.time())
    ttl = max(30, min(ttl_seconds or settings.runtime_token_ttl_seconds, 3600))
    claims = {
        "iss": settings.token_issuer,
        "aud": RUNTIME_TOKEN_AUDIENCE,
        "iat": now,
        "exp": now + ttl,
        "jti": secrets.token_hex(16),
        "job_id": job_id,
        "session_id": session_id,
        "tenant_id": principal.tenant_id,
        "user_id": principal.user_id,
        "role_ids": list(principal.role_ids),
        "data_scope": principal.data_scope or {},
        "allowed_tools": list(principal.allowed_tools),
        "trace_id": trace_id or "",
    }
    return _encode_token(claims)


def issue_api_token(principal: Principal, *, ttl_seconds: int = 900) -> str:
    """Helper for the platform's login/SSO integration; not exposed as a public endpoint."""
    now = int(time.time())
    claims = {
        "iss": settings.token_issuer,
        "aud": settings.api_token_audience,
        "iat": now,
        "exp": now + max(60, min(ttl_seconds, 3600)),
        "jti": secrets.token_hex(16),
        "tenant_id": principal.tenant_id,
        "user_id": principal.user_id,
        "role_ids": list(principal.role_ids),
        "data_scope": principal.data_scope or {},
        "allowed_tools": list(principal.allowed_tools),
    }
    return _encode_token(claims)


def scope_session_id(tenant_id: str, user_id: str, session_id: str) -> str:
    raw = session_id.strip()
    if not raw or len(raw) > 128:
        raise ValueError("session_id must contain 1-128 characters")
    return f"{session_scope_prefix(tenant_id, user_id)}{raw}"


def session_scope_prefix(tenant_id: str, user_id: str) -> str:
    prefix = hashlib.sha256(f"{tenant_id}\0{user_id}".encode("utf-8")).hexdigest()[:24]
    return f"{prefix}:"


def unscope_session_id(scoped_session_id: str) -> str:
    _, separator, raw = scoped_session_id.partition(":")
    return raw if separator else scoped_session_id


def verify_token(authorization: str, *, audience: str) -> dict[str, Any]:
    scheme, _, token = authorization.strip().partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="invalid authorization scheme")
    try:
        header_part, payload_part, signature_part = token.split(".")
        signed = f"{header_part}.{payload_part}".encode("ascii")
        expected = hmac.new(_token_secret(audience), signed, hashlib.sha256).digest()
        supplied = _b64decode(signature_part)
        if not hmac.compare_digest(expected, supplied):
            raise ValueError("signature mismatch")
        header = json.loads(_b64decode(header_part))
        claims = json.loads(_b64decode(payload_part))
        if header.get("alg") != "HS256" or header.get("typ") != "JWT":
            raise ValueError("unsupported token header")
        now = int(time.time())
        if int(claims.get("exp") or 0) <= now:
            raise ValueError("token expired")
        if int(claims.get("iat") or 0) > now + 30:
            raise ValueError("token issued in the future")
        if claims.get("aud") != audience or claims.get("iss") != settings.token_issuer:
            raise ValueError("token audience or issuer mismatch")
        if not claims.get("tenant_id") or not claims.get("user_id"):
            raise ValueError("token identity is incomplete")
        return claims
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=401, detail=f"invalid bearer token: {exc}") from exc


def _principal_from_claims(claims: dict[str, Any], *, auth_method: str) -> Principal:
    roles = claims.get("role_ids") if isinstance(claims.get("role_ids"), list) else []
    tools = claims.get("allowed_tools") if isinstance(claims.get("allowed_tools"), list) else []
    scope = claims.get("data_scope") if isinstance(claims.get("data_scope"), dict) else {}
    return Principal(
        tenant_id=str(claims["tenant_id"]),
        user_id=str(claims["user_id"]),
        role_ids=tuple(str(item) for item in roles),
        data_scope=scope,
        allowed_tools=tuple(str(item) for item in tools) or settings.default_agent_tools,
        auth_method=auth_method,
    )


def _encode_token(claims: dict[str, Any]) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    header_part = _b64encode(json.dumps(header, separators=(",", ":"), sort_keys=True).encode())
    payload_part = _b64encode(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode())
    signed = f"{header_part}.{payload_part}".encode("ascii")
    audience = str(claims.get("aud") or RUNTIME_TOKEN_AUDIENCE)
    signature = hmac.new(_token_secret(audience), signed, hashlib.sha256).digest()
    return f"{header_part}.{payload_part}.{_b64encode(signature)}"


def _token_secret(audience: str) -> bytes:
    value = settings.api_token_secret if audience == settings.api_token_audience else settings.runtime_token_secret
    if not value:
        if not _is_development():
            variable = "API_TOKEN_SECRET" if audience == settings.api_token_audience else "RUNTIME_TOKEN_SECRET"
            raise HTTPException(status_code=503, detail=f"{variable} is not configured")
        value = f"local-development-only-change-me:{audience}"
    return value.encode("utf-8")


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _is_development() -> bool:
    return settings.app_env.strip().lower() in {"local", "dev", "development", "test"}
