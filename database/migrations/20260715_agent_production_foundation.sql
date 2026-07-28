-- PRD Phase 1/2 production foundation. Apply after database/schema.sql.

ALTER TABLE agent_chat_jobs
    ADD COLUMN IF NOT EXISTS idempotency_key VARCHAR(128) NULL,
    ADD COLUMN IF NOT EXISTS trace_id VARCHAR(64) NULL,
    ADD COLUMN IF NOT EXISTS role_ids_json LONGTEXT NULL,
    ADD COLUMN IF NOT EXISTS data_scope_json LONGTEXT NULL,
    ADD COLUMN IF NOT EXISTS allowed_tools_json LONGTEXT NULL,
    ADD COLUMN IF NOT EXISTS auth_context_version INT NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS fencing_token BIGINT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS final_metadata_json LONGTEXT NULL;

CREATE UNIQUE INDEX uniq_agent_job_idempotency
    ON agent_chat_jobs (tenant_id, user_id, idempotency_key);

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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

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
    UNIQUE KEY uniq_agent_evidence_citation (job_id, citation_id),
    KEY idx_agent_evidence_hash (content_hash)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS agent_claim (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    job_id VARCHAR(64) NOT NULL,
    claim_id VARCHAR(64) NOT NULL,
    claim_text LONGTEXT NOT NULL,
    citation_ids_json LONGTEXT NOT NULL,
    validation_status VARCHAR(32) NOT NULL,
    created_at DOUBLE NOT NULL,
    UNIQUE KEY uniq_agent_claim (job_id, claim_id),
    KEY idx_agent_claim_status (validation_status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

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
    KEY idx_agent_tool_audit_job (job_id, created_at),
    KEY idx_agent_tool_audit_actor (tenant_id, user_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

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
    KEY idx_agent_context_session (session_id, created_at),
    KEY idx_agent_context_job (job_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS knowledge_document_version (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    dataset_id VARCHAR(128) NOT NULL,
    document_id VARCHAR(128) NOT NULL,
    version VARCHAR(64) NOT NULL,
    publication_status ENUM('uploaded','parsed','quality_review','pending_publish','published','withdrawn','deleted') NOT NULL,
    source_ref LONGTEXT NULL,
    metadata_json LONGTEXT NOT NULL,
    acl_json LONGTEXT NOT NULL,
    parser_version VARCHAR(128) NULL,
    index_version VARCHAR(128) NULL,
    content_hash CHAR(64) NULL,
    created_at DOUBLE NOT NULL,
    updated_at DOUBLE NOT NULL,
    UNIQUE KEY uniq_knowledge_document_version (dataset_id, document_id, version),
    KEY idx_knowledge_publication (publication_status, dataset_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
