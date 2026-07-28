from __future__ import annotations

import json
import hashlib
import threading
from typing import Any

from backend.app.config import settings
from backend.app.db import DatabaseUnavailable, mysql_connection
from backend.app.services.agent_jobs_types import AgentEvent, AgentJob


class AgentJobPersistence:
    def ensure_schema(self) -> None:
        raise NotImplementedError

    def save_job(self, job: AgentJob) -> None:
        raise NotImplementedError

    def create_job(self, job: AgentJob) -> AgentJob:
        """Atomically create a job or return its idempotent predecessor."""
        self.save_job(job)
        return job

    def load_by_idempotency(self, tenant_id: str, user_id: str, idempotency_key: str) -> AgentJob | None:
        raise NotImplementedError

    def stage_dispatch(self, job: AgentJob) -> None:
        self.save_job(job)

    def mark_dispatch_sent(self, job_id: str, fencing_token: int) -> None:
        return None

    def load_job(self, job_id: str) -> AgentJob | None:
        raise NotImplementedError

    def save_event(self, job_id: str, event: AgentEvent) -> None:
        raise NotImplementedError

    def load_jobs(self) -> list[AgentJob]:
        raise NotImplementedError

    def load_stale_jobs(self, lease_expired_before: float) -> list[AgentJob]:
        raise NotImplementedError

    def load_events_after(self, job_id: str, after_seq: int = 0) -> list[AgentEvent]:
        raise NotImplementedError


class MySQLAgentJobPersistence(AgentJobPersistence):
    def __init__(self) -> None:
        self.ensure_schema()

    def ensure_schema(self) -> None:
        with mysql_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS agent_chat_jobs (
                        id VARCHAR(64) PRIMARY KEY,
                        tenant_id VARCHAR(64) NOT NULL,
                        user_id VARCHAR(64) NOT NULL,
                        session_id VARCHAR(128) NOT NULL,
                        prompt LONGTEXT NOT NULL,
                        queue_key VARCHAR(256) NOT NULL,
                        estimated_cost INT NOT NULL,
                        max_turns INT NOT NULL,
                        trace_id VARCHAR(64) NULL,
                        idempotency_key VARCHAR(128) NULL,
                        role_ids_json LONGTEXT NOT NULL,
                        data_scope_json LONGTEXT NOT NULL,
                        allowed_tools_json LONGTEXT NOT NULL,
                        auth_context_version INT NOT NULL DEFAULT 1,
                        status VARCHAR(32) NOT NULL,
                        priority INT NOT NULL DEFAULT 0,
                        attempt_count INT NOT NULL DEFAULT 0,
                        execution_token VARCHAR(128) NULL,
                        fencing_token BIGINT NOT NULL DEFAULT 0,
                        created_at DOUBLE NOT NULL,
                        queued_at DOUBLE NOT NULL,
                        started_at DOUBLE NULL,
                        finished_at DOUBLE NULL,
                        heartbeat_at DOUBLE NULL,
                        lease_expires_at DOUBLE NULL,
                        assigned_worker_id VARCHAR(128) NULL,
                        error_message LONGTEXT NULL,
                        final_text LONGTEXT NULL,
                        final_metadata_json LONGTEXT NULL,
                        input_tokens INT NOT NULL DEFAULT 0,
                        output_tokens INT NOT NULL DEFAULT 0,
                        tool_call_count INT NOT NULL DEFAULT 0,
                        updated_at DOUBLE NOT NULL,
                        KEY idx_agent_jobs_status (status, queued_at),
                        KEY idx_agent_jobs_session (session_id, status),
                        KEY idx_agent_jobs_queue_key (queue_key, queued_at),
                        KEY idx_agent_jobs_lease (status, lease_expires_at)
                        ,UNIQUE KEY uniq_agent_job_idempotency (tenant_id, user_id, idempotency_key)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                    """
                )
                _ensure_column(cursor, "agent_chat_jobs", "attempt_count", "INT NOT NULL DEFAULT 0")
                _ensure_column(cursor, "agent_chat_jobs", "execution_token", "VARCHAR(128) NULL")
                _ensure_column(cursor, "agent_chat_jobs", "heartbeat_at", "DOUBLE NULL")
                _ensure_column(cursor, "agent_chat_jobs", "lease_expires_at", "DOUBLE NULL")
                _ensure_column(cursor, "agent_chat_jobs", "idempotency_key", "VARCHAR(128) NULL")
                _ensure_column(cursor, "agent_chat_jobs", "trace_id", "VARCHAR(64) NULL")
                _ensure_column(cursor, "agent_chat_jobs", "role_ids_json", "LONGTEXT NULL")
                _ensure_column(cursor, "agent_chat_jobs", "data_scope_json", "LONGTEXT NULL")
                _ensure_column(cursor, "agent_chat_jobs", "allowed_tools_json", "LONGTEXT NULL")
                _ensure_column(cursor, "agent_chat_jobs", "auth_context_version", "INT NOT NULL DEFAULT 1")
                _ensure_column(cursor, "agent_chat_jobs", "fencing_token", "BIGINT NOT NULL DEFAULT 0")
                _ensure_column(cursor, "agent_chat_jobs", "final_metadata_json", "LONGTEXT NULL")
                _ensure_unique_index(
                    cursor,
                    "agent_chat_jobs",
                    "uniq_agent_job_idempotency",
                    "tenant_id, user_id, idempotency_key",
                )
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS agent_chat_events (
                        id BIGINT PRIMARY KEY AUTO_INCREMENT,
                        job_id VARCHAR(64) NOT NULL,
                        seq INT NOT NULL,
                        event_type VARCHAR(64) NOT NULL,
                        payload_json LONGTEXT NOT NULL,
                        created_at DOUBLE NOT NULL,
                        UNIQUE KEY uniq_agent_job_seq (job_id, seq),
                        KEY idx_agent_events_job (job_id, seq)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                    """
                )
                _ensure_execution_metadata_schema(cursor)
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS agent_job_outbox (
                        id BIGINT PRIMARY KEY AUTO_INCREMENT,
                        job_id VARCHAR(64) NOT NULL,
                        fencing_token BIGINT NOT NULL,
                        execution_token VARCHAR(128) NOT NULL,
                        status VARCHAR(16) NOT NULL DEFAULT 'pending',
                        created_at DOUBLE NOT NULL,
                        sent_at DOUBLE NULL,
                        UNIQUE KEY uniq_agent_dispatch_fence (job_id, fencing_token),
                        KEY idx_agent_outbox_status (status, created_at)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                    """
                )

    def save_job(self, job: AgentJob) -> None:
        payload = _job_payload(job)
        with mysql_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO agent_chat_jobs (
                        id, tenant_id, user_id, session_id, prompt, queue_key,
                        estimated_cost, max_turns, trace_id, idempotency_key, role_ids_json, data_scope_json,
                        allowed_tools_json, auth_context_version, status, priority, attempt_count,
                        execution_token, fencing_token, created_at,
                        queued_at, started_at, finished_at,
                        heartbeat_at, lease_expires_at, assigned_worker_id,
                        error_message, final_text, final_metadata_json, input_tokens, output_tokens,
                        tool_call_count, updated_at
                    )
                    VALUES (
                        %(id)s, %(tenant_id)s, %(user_id)s, %(session_id)s, %(prompt)s, %(queue_key)s,
                        %(estimated_cost)s, %(max_turns)s, %(trace_id)s, %(idempotency_key)s, %(role_ids_json)s, %(data_scope_json)s,
                        %(allowed_tools_json)s, %(auth_context_version)s, %(status)s, %(priority)s, %(attempt_count)s,
                        %(execution_token)s, %(fencing_token)s, %(created_at)s,
                        %(queued_at)s, %(started_at)s, %(finished_at)s, %(heartbeat_at)s, %(lease_expires_at)s, %(assigned_worker_id)s,
                        %(error_message)s, %(final_text)s, %(final_metadata_json)s, %(input_tokens)s, %(output_tokens)s,
                        %(tool_call_count)s, %(updated_at)s
                    )
                    ON DUPLICATE KEY UPDATE
                        tenant_id=VALUES(tenant_id),
                        user_id=VALUES(user_id),
                        session_id=VALUES(session_id),
                        prompt=VALUES(prompt),
                        queue_key=VALUES(queue_key),
                        estimated_cost=VALUES(estimated_cost),
                        max_turns=VALUES(max_turns),
                        trace_id=IF(VALUES(fencing_token) >= fencing_token, VALUES(trace_id), trace_id),
                        idempotency_key=COALESCE(idempotency_key, VALUES(idempotency_key)),
                        role_ids_json=IF(VALUES(fencing_token) >= fencing_token, VALUES(role_ids_json), role_ids_json),
                        data_scope_json=IF(VALUES(fencing_token) >= fencing_token, VALUES(data_scope_json), data_scope_json),
                        allowed_tools_json=IF(VALUES(fencing_token) >= fencing_token, VALUES(allowed_tools_json), allowed_tools_json),
                        auth_context_version=IF(VALUES(fencing_token) >= fencing_token, VALUES(auth_context_version), auth_context_version),
                        status=IF(VALUES(fencing_token) >= fencing_token, VALUES(status), status),
                        priority=IF(VALUES(fencing_token) >= fencing_token, VALUES(priority), priority),
                        attempt_count=IF(VALUES(fencing_token) >= fencing_token, VALUES(attempt_count), attempt_count),
                        execution_token=IF(VALUES(fencing_token) >= fencing_token, VALUES(execution_token), execution_token),
                        queued_at=IF(VALUES(fencing_token) >= fencing_token, VALUES(queued_at), queued_at),
                        started_at=IF(VALUES(fencing_token) >= fencing_token, VALUES(started_at), started_at),
                        finished_at=IF(VALUES(fencing_token) >= fencing_token, VALUES(finished_at), finished_at),
                        heartbeat_at=IF(VALUES(fencing_token) >= fencing_token, VALUES(heartbeat_at), heartbeat_at),
                        lease_expires_at=IF(VALUES(fencing_token) >= fencing_token, VALUES(lease_expires_at), lease_expires_at),
                        assigned_worker_id=IF(VALUES(fencing_token) >= fencing_token, VALUES(assigned_worker_id), assigned_worker_id),
                        error_message=IF(VALUES(fencing_token) >= fencing_token, VALUES(error_message), error_message),
                        final_text=IF(VALUES(fencing_token) >= fencing_token, VALUES(final_text), final_text),
                        final_metadata_json=IF(VALUES(fencing_token) >= fencing_token, VALUES(final_metadata_json), final_metadata_json),
                        input_tokens=IF(VALUES(fencing_token) >= fencing_token, VALUES(input_tokens), input_tokens),
                        output_tokens=IF(VALUES(fencing_token) >= fencing_token, VALUES(output_tokens), output_tokens),
                        tool_call_count=IF(VALUES(fencing_token) >= fencing_token, VALUES(tool_call_count), tool_call_count),
                        updated_at=IF(VALUES(fencing_token) >= fencing_token, VALUES(updated_at), updated_at),
                        fencing_token=GREATEST(fencing_token, VALUES(fencing_token))
                    """,
                    payload,
                )
                if job.final_metadata:
                    _save_execution_metadata(cursor, job)

    def create_job(self, job: AgentJob) -> AgentJob:
        payload = _job_payload(job)
        try:
            with mysql_connection() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        INSERT INTO agent_chat_jobs (
                            id, tenant_id, user_id, session_id, prompt, queue_key,
                            estimated_cost, max_turns, trace_id, idempotency_key, role_ids_json, data_scope_json,
                            allowed_tools_json, auth_context_version, status, priority, attempt_count,
                            execution_token, fencing_token, created_at, queued_at, started_at, finished_at,
                            heartbeat_at, lease_expires_at, assigned_worker_id, error_message, final_text,
                            final_metadata_json, input_tokens, output_tokens, tool_call_count, updated_at
                        ) VALUES (
                            %(id)s, %(tenant_id)s, %(user_id)s, %(session_id)s, %(prompt)s, %(queue_key)s,
                            %(estimated_cost)s, %(max_turns)s, %(trace_id)s, %(idempotency_key)s, %(role_ids_json)s, %(data_scope_json)s,
                            %(allowed_tools_json)s, %(auth_context_version)s, %(status)s, %(priority)s, %(attempt_count)s,
                            %(execution_token)s, %(fencing_token)s, %(created_at)s, %(queued_at)s, %(started_at)s, %(finished_at)s,
                            %(heartbeat_at)s, %(lease_expires_at)s, %(assigned_worker_id)s, %(error_message)s, %(final_text)s,
                            %(final_metadata_json)s, %(input_tokens)s, %(output_tokens)s, %(tool_call_count)s, %(updated_at)s
                        )
                        """,
                        payload,
                    )
        except Exception:
            if job.idempotency_key:
                existing = self.load_by_idempotency(job.tenant_id, job.user_id, job.idempotency_key)
                if existing is not None:
                    return existing
            raise
        if job.idempotency_key:
            return self.load_by_idempotency(job.tenant_id, job.user_id, job.idempotency_key) or job
        return job

    def load_by_idempotency(self, tenant_id: str, user_id: str, idempotency_key: str) -> AgentJob | None:
        with mysql_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT * FROM agent_chat_jobs WHERE tenant_id=%s AND user_id=%s AND idempotency_key=%s LIMIT 1",
                    (tenant_id, user_id, idempotency_key),
                )
                row = cursor.fetchone()
        return AgentJob.from_row(row) if row else None

    def stage_dispatch(self, job: AgentJob) -> None:
        payload = _job_payload(job)
        with mysql_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE agent_chat_jobs SET
                        status=%(status)s, attempt_count=%(attempt_count)s,
                        execution_token=%(execution_token)s, fencing_token=%(fencing_token)s,
                        heartbeat_at=%(heartbeat_at)s, lease_expires_at=%(lease_expires_at)s,
                        updated_at=%(updated_at)s
                    WHERE id=%(id)s AND fencing_token <= %(fencing_token)s
                    """,
                    payload,
                )
                if cursor.rowcount != 1:
                    raise RuntimeError("dispatch fencing update was rejected")
                cursor.execute(
                    """
                    INSERT INTO agent_job_outbox
                        (job_id, fencing_token, execution_token, status, created_at)
                    VALUES (%s, %s, %s, 'pending', %s)
                    ON DUPLICATE KEY UPDATE execution_token=VALUES(execution_token)
                    """,
                    (job.id, job.fencing_token, job.execution_token, time_now()),
                )

    def mark_dispatch_sent(self, job_id: str, fencing_token: int) -> None:
        with mysql_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE agent_job_outbox SET status='sent', sent_at=%s
                    WHERE job_id=%s AND fencing_token=%s AND status='pending'
                    """,
                    (time_now(), job_id, fencing_token),
                )

    def load_job(self, job_id: str) -> AgentJob | None:
        with mysql_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT * FROM agent_chat_jobs WHERE id=%s LIMIT 1", (job_id,))
                row = cursor.fetchone()
        if not row:
            return None
        return AgentJob.from_row(row)

    def save_event(self, job_id: str, event: AgentEvent) -> None:
        with mysql_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO agent_chat_events (job_id, seq, event_type, payload_json, created_at)
                    VALUES (%s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        event_type=VALUES(event_type),
                        payload_json=VALUES(payload_json),
                        created_at=VALUES(created_at)
                    """,
                    (
                        job_id,
                        event.seq,
                        event.event_type,
                        json.dumps(event.payload, ensure_ascii=False, default=str),
                        event.created_at,
                    ),
                )

    def load_jobs(self) -> list[AgentJob]:
        with mysql_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT * FROM agent_chat_jobs
                    WHERE status IN ('queued', 'admitted', 'running', 'cancel_requested')
                    ORDER BY created_at ASC
                    """
                )
                rows = cursor.fetchall() or []
        return [AgentJob.from_row(row) for row in rows]

    def load_stale_jobs(self, lease_expired_before: float) -> list[AgentJob]:
        with mysql_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT * FROM agent_chat_jobs
                    WHERE status IN ('admitted', 'running')
                      AND lease_expires_at IS NOT NULL
                      AND lease_expires_at < %s
                    ORDER BY lease_expires_at ASC
                    """,
                    (lease_expired_before,),
                )
                rows = cursor.fetchall() or []
        return [AgentJob.from_row(row) for row in rows]

    def load_events_after(self, job_id: str, after_seq: int = 0) -> list[AgentEvent]:
        with mysql_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT seq, event_type, payload_json AS payload, created_at FROM agent_chat_events WHERE job_id=%s AND seq > %s ORDER BY seq ASC",
                    (job_id, after_seq),
                )
                rows = cursor.fetchall() or []
        events: list[AgentEvent] = []
        for row in rows:
            payload = row.get("payload")
            try:
                parsed = json.loads(payload) if isinstance(payload, str) else {}
            except Exception:
                parsed = {}
            events.append(
                AgentEvent(
                    seq=int(row["seq"]),
                    event_type=str(row["event_type"]),
                    payload=parsed if isinstance(parsed, dict) else {},
                    created_at=float(row["created_at"]),
                )
            )
        return events


def _job_payload(job: AgentJob) -> dict[str, Any]:
    return {
        "id": job.id,
        "tenant_id": job.tenant_id,
        "user_id": job.user_id,
        "session_id": job.session_id,
        "prompt": job.prompt,
        "queue_key": job.queue_key,
        "estimated_cost": job.estimated_cost,
        "max_turns": job.max_turns,
        "trace_id": job.trace_id,
        "idempotency_key": job.idempotency_key,
        "role_ids_json": json.dumps(list(job.role_ids_snapshot), ensure_ascii=False),
        "data_scope_json": json.dumps(job.data_scope_snapshot, ensure_ascii=False, sort_keys=True),
        "allowed_tools_json": json.dumps(list(job.allowed_tools_snapshot), ensure_ascii=False),
        "auth_context_version": job.auth_context_version,
        "status": job.status.value,
        "priority": job.priority,
        "attempt_count": job.attempt_count,
        "execution_token": job.execution_token,
        "fencing_token": job.fencing_token,
        "created_at": job.created_at,
        "queued_at": job.queued_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "heartbeat_at": job.heartbeat_at,
        "lease_expires_at": job.lease_expires_at,
        "assigned_worker_id": job.assigned_worker_id,
        "error_message": job.error_message,
        "final_text": job.final_text,
        "final_metadata_json": json.dumps(job.final_metadata, ensure_ascii=False, sort_keys=True, default=str),
        "input_tokens": job.input_tokens,
        "output_tokens": job.output_tokens,
        "tool_call_count": job.tool_call_count,
        "updated_at": time_now(),
    }


def time_now() -> float:
    import time

    return time.time()


def _ensure_column(cursor: Any, table: str, column: str, definition: str) -> None:
    cursor.execute(
        """
        SELECT COUNT(*) AS cnt
        FROM information_schema.columns
        WHERE table_schema = DATABASE()
          AND table_name = %s
          AND column_name = %s
        """,
        (table, column),
    )
    row = cursor.fetchone() or {}
    if int(row.get("cnt") or 0):
        return
    cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _ensure_unique_index(cursor: Any, table: str, name: str, columns: str) -> None:
    cursor.execute(
        "SELECT COUNT(*) AS cnt FROM information_schema.statistics "
        "WHERE table_schema=DATABASE() AND table_name=%s AND index_name=%s",
        (table, name),
    )
    if int((cursor.fetchone() or {}).get("cnt") or 0):
        return
    cursor.execute(f"ALTER TABLE {table} ADD UNIQUE KEY {name} ({columns})")


def _ensure_execution_metadata_schema(cursor: Any) -> None:
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_evidence_snapshot (
            id BIGINT PRIMARY KEY AUTO_INCREMENT,
            job_id VARCHAR(64) NOT NULL,
            citation_id INT NOT NULL,
            evidence_type VARCHAR(32) NOT NULL,
            source_ref LONGTEXT NULL,
            content_snapshot LONGTEXT NOT NULL,
            metadata_json LONGTEXT NOT NULL,
            content_hash CHAR(64) NOT NULL,
            data_version VARCHAR(128) NULL,
            acl_snapshot_json LONGTEXT NULL,
            created_at DOUBLE NOT NULL,
            UNIQUE KEY uniq_agent_evidence_citation (job_id, citation_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_claim (
            id BIGINT PRIMARY KEY AUTO_INCREMENT,
            job_id VARCHAR(64) NOT NULL,
            claim_id VARCHAR(64) NOT NULL,
            claim_text LONGTEXT NOT NULL,
            citation_ids_json LONGTEXT NOT NULL,
            validation_status VARCHAR(32) NOT NULL,
            created_at DOUBLE NOT NULL,
            UNIQUE KEY uniq_agent_claim (job_id, claim_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_tool_audit (
            id BIGINT PRIMARY KEY AUTO_INCREMENT,
            tenant_id VARCHAR(64) NOT NULL,
            user_id VARCHAR(64) NOT NULL,
            job_id VARCHAR(64) NOT NULL,
            trace_id VARCHAR(64) NULL,
            tool_name VARCHAR(128) NOT NULL,
            authorization_decision VARCHAR(16) NOT NULL,
            reason VARCHAR(512) NULL,
            resource_refs_json LONGTEXT NULL,
            created_at DOUBLE NOT NULL,
            KEY idx_agent_tool_audit_job (job_id, created_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_context_snapshot (
            id BIGINT PRIMARY KEY AUTO_INCREMENT,
            job_id VARCHAR(64) NOT NULL,
            session_id VARCHAR(128) NOT NULL,
            schema_version INT NOT NULL DEFAULT 1,
            snapshot_json LONGTEXT NOT NULL,
            before_tokens INT NOT NULL,
            after_tokens INT NOT NULL,
            content_hash CHAR(64) NOT NULL,
            created_at DOUBLE NOT NULL,
            KEY idx_agent_context_job (job_id),
            KEY idx_agent_context_session (session_id, created_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )


def _save_execution_metadata(cursor: Any, job: AgentJob) -> None:
    metadata = job.final_metadata
    citations = metadata.get("citations") if isinstance(metadata.get("citations"), list) else []
    for citation in citations:
        if not isinstance(citation, dict):
            continue
        cursor.execute(
            """
            INSERT INTO agent_evidence_snapshot
                (job_id, citation_id, evidence_type, source_ref, content_snapshot,
                 metadata_json, content_hash, data_version, acl_snapshot_json, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                source_ref=VALUES(source_ref), content_snapshot=VALUES(content_snapshot),
                metadata_json=VALUES(metadata_json), content_hash=VALUES(content_hash),
                data_version=VALUES(data_version), acl_snapshot_json=VALUES(acl_snapshot_json)
            """,
            (
                job.id,
                int(citation.get("citation_id") or 0),
                str(citation.get("source_type") or "other")[:32],
                str(citation.get("source") or ""),
                str(citation.get("content") or ""),
                json.dumps(citation.get("metadata") or {}, ensure_ascii=False, default=str),
                str(citation.get("evidence_hash") or "")[:64].ljust(64, "0"),
                str((citation.get("metadata") or {}).get("data_version") or "")[:128] or None,
                json.dumps(job.data_scope_snapshot, ensure_ascii=False, default=str),
                time_now(),
            ),
        )
    claims = metadata.get("claims") if isinstance(metadata.get("claims"), list) else []
    for claim in claims:
        if not isinstance(claim, dict):
            continue
        cursor.execute(
            """
            INSERT INTO agent_claim
                (job_id, claim_id, claim_text, citation_ids_json, validation_status, created_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                claim_text=VALUES(claim_text), citation_ids_json=VALUES(citation_ids_json),
                validation_status=VALUES(validation_status)
            """,
            (
                job.id,
                str(claim.get("claim_id") or "")[:64],
                str(claim.get("text") or ""),
                json.dumps(claim.get("citation_ids") or []),
                str(claim.get("status") or "unsupported")[:32],
                time_now(),
            ),
        )
    audits = metadata.get("tool_audit") if isinstance(metadata.get("tool_audit"), list) else []
    cursor.execute("DELETE FROM agent_tool_audit WHERE job_id=%s", (job.id,))
    cursor.execute("DELETE FROM agent_context_snapshot WHERE job_id=%s", (job.id,))
    for audit in audits:
        if not isinstance(audit, dict):
            continue
        if audit.get("event") == "context_compacted":
            snapshot_json = json.dumps(audit, ensure_ascii=False, sort_keys=True, default=str)
            cursor.execute(
                """
                INSERT INTO agent_context_snapshot
                    (job_id, session_id, schema_version, snapshot_json,
                     before_tokens, after_tokens, content_hash, created_at)
                VALUES (%s, %s, 1, %s, %s, %s, %s, %s)
                """,
                (
                    job.id,
                    job.session_id,
                    snapshot_json,
                    int(audit.get("before_tokens") or 0),
                    int(audit.get("after_tokens") or 0),
                    hashlib.sha256(snapshot_json.encode("utf-8")).hexdigest(),
                    time_now(),
                ),
            )
            continue
        if not audit.get("tool_name"):
            continue
        cursor.execute(
            """
            INSERT INTO agent_tool_audit
                (tenant_id, user_id, job_id, trace_id, tool_name,
                 authorization_decision, reason, resource_refs_json, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                job.tenant_id,
                job.user_id,
                job.id,
                str(audit.get("trace_id") or "")[:64] or None,
                str(audit.get("tool_name") or "")[:128],
                str(audit.get("authorization_decision") or "deny")[:16],
                str(audit.get("reason") or "")[:512] or None,
                json.dumps(audit.get("resource_refs") or [], ensure_ascii=False, default=str),
                time_now(),
            ),
        )


class MemoryAgentJobPersistence(AgentJobPersistence):
    def __init__(self) -> None:
        self._jobs: dict[str, AgentJob] = {}
        self._events: dict[str, dict[int, AgentEvent]] = {}
        self._idempotency: dict[tuple[str, str, str], str] = {}
        self._lock = threading.RLock()
        self._outbox: dict[tuple[str, int], str] = {}

    def ensure_schema(self) -> None:
        return None

    def create_job(self, job: AgentJob) -> AgentJob:
        with self._lock:
            if job.idempotency_key:
                key = (job.tenant_id, job.user_id, job.idempotency_key)
                existing_id = self._idempotency.get(key)
                if existing_id:
                    return self._jobs[existing_id]
                self._idempotency[key] = job.id
            self._jobs[job.id] = job
            return job

    def save_job(self, job: AgentJob) -> None:
        with self._lock:
            current = self._jobs.get(job.id)
            if current is None or job.fencing_token >= current.fencing_token:
                self._jobs[job.id] = job

    def load_job(self, job_id: str) -> AgentJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def load_by_idempotency(self, tenant_id: str, user_id: str, idempotency_key: str) -> AgentJob | None:
        with self._lock:
            job_id = self._idempotency.get((tenant_id, user_id, idempotency_key))
            return self._jobs.get(job_id) if job_id else None

    def save_event(self, job_id: str, event: AgentEvent) -> None:
        with self._lock:
            self._events.setdefault(job_id, {})[event.seq] = event

    def load_jobs(self) -> list[AgentJob]:
        with self._lock:
            return [job for job in self._jobs.values() if job.status.value in {"queued", "admitted", "running", "cancel_requested"}]

    def load_stale_jobs(self, lease_expired_before: float) -> list[AgentJob]:
        with self._lock:
            return [
                job for job in self._jobs.values()
                if job.status.value in {"admitted", "running"}
                and job.lease_expires_at is not None
                and job.lease_expires_at < lease_expired_before
            ]

    def load_events_after(self, job_id: str, after_seq: int = 0) -> list[AgentEvent]:
        with self._lock:
            events = self._events.get(job_id, {})
            return [events[seq] for seq in sorted(events) if seq > after_seq]

    def stage_dispatch(self, job: AgentJob) -> None:
        with self._lock:
            self.save_job(job)
            self._outbox[(job.id, job.fencing_token)] = "pending"

    def mark_dispatch_sent(self, job_id: str, fencing_token: int) -> None:
        with self._lock:
            key = (job_id, fencing_token)
            if key in self._outbox:
                self._outbox[key] = "sent"


def build_agent_job_persistence() -> AgentJobPersistence:
    if settings.data_backend.lower() == "memory":
        return MemoryAgentJobPersistence()
    return MySQLAgentJobPersistence()
