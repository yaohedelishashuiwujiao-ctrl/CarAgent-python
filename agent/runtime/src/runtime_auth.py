from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any


RUNTIME_AUDIENCE = "subjects-agent-runtime"


class RuntimeAuthError(ValueError):
    pass


def verify_runtime_authorization(authorization: str, *, session_id: str) -> dict[str, Any]:
    scheme, _, token = (authorization or "").strip().partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise RuntimeAuthError("signed runtime bearer token is required")
    try:
        header_part, payload_part, signature_part = token.split(".")
        signed = f"{header_part}.{payload_part}".encode("ascii")
        expected = hmac.new(_secret(), signed, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _decode(signature_part)):
            raise RuntimeAuthError("runtime token signature mismatch")
        header = json.loads(_decode(header_part))
        claims = json.loads(_decode(payload_part))
        now = int(time.time())
        if header.get("alg") != "HS256" or header.get("typ") != "JWT":
            raise RuntimeAuthError("unsupported runtime token")
        if claims.get("aud") != RUNTIME_AUDIENCE:
            raise RuntimeAuthError("runtime token audience mismatch")
        if claims.get("iss") != os.getenv("TOKEN_ISSUER", "subjects-platform"):
            raise RuntimeAuthError("runtime token issuer mismatch")
        if int(claims.get("exp") or 0) <= now:
            raise RuntimeAuthError("runtime token expired")
        if int(claims.get("iat") or 0) > now + 30:
            raise RuntimeAuthError("runtime token issued in the future")
        if str(claims.get("session_id") or "") != session_id:
            raise RuntimeAuthError("runtime token session mismatch")
        for required in ("job_id", "tenant_id", "user_id", "jti"):
            if not claims.get(required):
                raise RuntimeAuthError(f"runtime token is missing {required}")
        return claims
    except RuntimeAuthError:
        raise
    except Exception as exc:
        raise RuntimeAuthError(f"invalid runtime token: {exc}") from exc


def local_runtime_context(session_id: str) -> dict[str, Any]:
    if not _is_development() or os.getenv("ALLOW_INSECURE_DEV_AUTH", "true").lower() not in {"1", "true", "yes", "on"}:
        raise RuntimeAuthError("signed runtime bearer token is required")
    return {
        "job_id": "local-direct",
        "session_id": session_id,
        "tenant_id": "default",
        "user_id": "anonymous",
        "role_ids": ["user"],
        "data_scope": {"scope": "published", "source": "local-direct"},
        "allowed_tools": _default_tools(),
        "jti": "local-direct",
    }


def _default_tools() -> list[str]:
    value = os.getenv(
        "AGENT_DEFAULT_ALLOWED_TOOLS",
        "TodoWrite,SubjectsAttributeLookup,SubjectsAttributeStats,SubjectsSqlSchema,SubjectsSqlGlob,SubjectsSqlQuery,KnowledgeSearch,KnowledgeFetch,WebSearch,WebFetch,AutoChartGenerate,AutoPptxGenerate,StructuredOutput,SendUserMessage",
    )
    return [item.strip() for item in value.split(",") if item.strip()]


def _secret() -> bytes:
    value = os.getenv("RUNTIME_TOKEN_SECRET", "")
    if not value:
        if not _is_development():
            raise RuntimeAuthError("RUNTIME_TOKEN_SECRET is not configured")
        value = "local-development-only-change-me:subjects-agent-runtime"
    return value.encode("utf-8")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _is_development() -> bool:
    return os.getenv("APP_ENV", "local").strip().lower() in {"local", "dev", "development", "test"}
