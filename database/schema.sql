SET NAMES utf8mb4;

CREATE TABLE entity_type (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    category VARCHAR(32) NOT NULL,
    code VARCHAR(128) NOT NULL UNIQUE,
    name VARCHAR(128) NOT NULL,
    description VARCHAR(512),
    is_builtin BOOLEAN NOT NULL DEFAULT FALSE,
    status VARCHAR(32) NOT NULL DEFAULT 'active',
    sort_order INT NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    KEY idx_entity_type_category (category, status)
);

CREATE TABLE system_catalog (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    code VARCHAR(64) NOT NULL UNIQUE,
    name VARCHAR(128) NOT NULL,
    description VARCHAR(512),
    is_builtin BOOLEAN NOT NULL DEFAULT TRUE,
    status VARCHAR(32) NOT NULL DEFAULT 'active',
    sort_order INT NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

CREATE TABLE component_entity_system (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    entity_type_id BIGINT NOT NULL,
    system_id BIGINT NOT NULL,
    is_default BOOLEAN NOT NULL DEFAULT TRUE,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_component_entity_system (entity_type_id, system_id),
    CONSTRAINT fk_component_entity_type FOREIGN KEY (entity_type_id) REFERENCES entity_type(id),
    CONSTRAINT fk_component_system FOREIGN KEY (system_id) REFERENCES system_catalog(id)
);

CREATE TABLE entity_attribute_group (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    entity_type_id BIGINT NOT NULL,
    code VARCHAR(64) NOT NULL,
    name VARCHAR(128) NOT NULL,
    sort_order INT NOT NULL DEFAULT 0,
    status VARCHAR(32) NOT NULL DEFAULT 'active',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_attr_group_entity_code (entity_type_id, code),
    CONSTRAINT fk_attr_group_entity_type FOREIGN KEY (entity_type_id) REFERENCES entity_type(id)
);

CREATE TABLE entity_attribute (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    entity_type_id BIGINT NOT NULL,
    group_id BIGINT,
    code VARCHAR(64) NOT NULL,
    name VARCHAR(128) NOT NULL,
    attr_type VARCHAR(32) NOT NULL,
    unit VARCHAR(32),
    is_required BOOLEAN NOT NULL DEFAULT FALSE,
    is_searchable BOOLEAN NOT NULL DEFAULT FALSE,
    is_importable BOOLEAN NOT NULL DEFAULT TRUE,
    is_exportable BOOLEAN NOT NULL DEFAULT TRUE,
    is_multi_value BOOLEAN NOT NULL DEFAULT FALSE,
    is_builtin BOOLEAN NOT NULL DEFAULT FALSE,
    config_json JSON,
    version INT NOT NULL DEFAULT 1,
    status VARCHAR(32) NOT NULL DEFAULT 'active',
    sort_order INT NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_attr_entity_code_version (entity_type_id, code, version),
    KEY idx_attr_searchable (entity_type_id, is_searchable),
    CONSTRAINT fk_attr_entity_type FOREIGN KEY (entity_type_id) REFERENCES entity_type(id),
    CONSTRAINT fk_attr_group FOREIGN KEY (group_id) REFERENCES entity_attribute_group(id)
);

CREATE TABLE entity_attribute_option (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    attribute_id BIGINT NOT NULL,
    option_value VARCHAR(128) NOT NULL,
    option_label VARCHAR(128) NOT NULL,
    sort_order INT NOT NULL DEFAULT 0,
    status VARCHAR(32) NOT NULL DEFAULT 'active',
    UNIQUE KEY uk_attr_option_value (attribute_id, option_value),
    CONSTRAINT fk_attr_option_attribute FOREIGN KEY (attribute_id) REFERENCES entity_attribute(id)
);

CREATE TABLE vehicle_instance (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    entity_type_id BIGINT NOT NULL,
    vehicle_code VARCHAR(128) NOT NULL UNIQUE,
    vehicle_name VARCHAR(255) NOT NULL,
    source_type VARCHAR(32) NOT NULL DEFAULT 'manual',
    owner_user_id BIGINT,
    status VARCHAR(32) NOT NULL DEFAULT 'active',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    KEY idx_vehicle_name (vehicle_name),
    KEY idx_vehicle_status_id (status, id),
    KEY idx_vehicle_source_status_id (source_type, status, id),
    CONSTRAINT fk_vehicle_entity_type FOREIGN KEY (entity_type_id) REFERENCES entity_type(id)
);

CREATE TABLE vehicle_system_profile (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    vehicle_instance_id BIGINT NOT NULL,
    system_id BIGINT NOT NULL,
    profile_name VARCHAR(255) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'active',
    sort_order INT NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_vehicle_system (vehicle_instance_id, system_id),
    CONSTRAINT fk_vehicle_system_vehicle FOREIGN KEY (vehicle_instance_id) REFERENCES vehicle_instance(id),
    CONSTRAINT fk_vehicle_system_system FOREIGN KEY (system_id) REFERENCES system_catalog(id)
);

CREATE TABLE component_instance (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    vehicle_instance_id BIGINT NOT NULL,
    system_id BIGINT NOT NULL,
    entity_type_id BIGINT NOT NULL,
    component_code VARCHAR(128) NOT NULL,
    component_name VARCHAR(255) NOT NULL,
    source_type VARCHAR(32) NOT NULL DEFAULT 'manual',
    status VARCHAR(32) NOT NULL DEFAULT 'active',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_component_vehicle_code (vehicle_instance_id, component_code),
    KEY idx_component_vehicle_system (vehicle_instance_id, system_id),
    KEY idx_component_entity_type (entity_type_id),
    CONSTRAINT fk_component_vehicle FOREIGN KEY (vehicle_instance_id) REFERENCES vehicle_instance(id),
    CONSTRAINT fk_component_system_ref FOREIGN KEY (system_id) REFERENCES system_catalog(id),
    CONSTRAINT fk_component_instance_entity_type FOREIGN KEY (entity_type_id) REFERENCES entity_type(id)
);

CREATE TABLE instance_attribute_value (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    target_type VARCHAR(32) NOT NULL,
    target_id BIGINT NOT NULL,
    attribute_id BIGINT NOT NULL,
    value_text TEXT,
    value_number DECIMAL(20, 6),
    value_datetime DATETIME,
    value_boolean BOOLEAN,
    value_json JSON,
    value_ref_id BIGINT,
    unit VARCHAR(32),
    value_source VARCHAR(32) NOT NULL DEFAULT 'manual',
    confidence DECIMAL(5, 4),
    version INT NOT NULL DEFAULT 1,
    status VARCHAR(32) NOT NULL DEFAULT 'active',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    KEY idx_value_target (target_type, target_id),
    KEY idx_value_attribute (attribute_id),
    KEY idx_value_attribute_text_target (attribute_id, value_text(128), target_type, target_id),
    KEY idx_value_number (attribute_id, value_number),
    CONSTRAINT fk_instance_value_attribute FOREIGN KEY (attribute_id) REFERENCES entity_attribute(id)
);

CREATE TABLE system_attribute_value (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    vehicle_system_profile_id BIGINT NOT NULL,
    attribute_id BIGINT NOT NULL,
    value_text TEXT,
    value_number DECIMAL(20, 6),
    value_datetime DATETIME,
    value_boolean BOOLEAN,
    value_json JSON,
    unit VARCHAR(32),
    value_source VARCHAR(32) NOT NULL DEFAULT 'manual',
    confidence DECIMAL(5, 4),
    version INT NOT NULL DEFAULT 1,
    status VARCHAR(32) NOT NULL DEFAULT 'active',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    KEY idx_system_value_profile (vehicle_system_profile_id),
    KEY idx_system_value_attribute (attribute_id),
    CONSTRAINT fk_system_value_profile FOREIGN KEY (vehicle_system_profile_id) REFERENCES vehicle_system_profile(id),
    CONSTRAINT fk_system_value_attribute FOREIGN KEY (attribute_id) REFERENCES entity_attribute(id)
);

CREATE TABLE media_asset (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    owner_type VARCHAR(32) NOT NULL,
    owner_id BIGINT NOT NULL,
    media_type VARCHAR(32) NOT NULL,
    file_name VARCHAR(255) NOT NULL,
    file_url VARCHAR(1024) NOT NULL,
    mime_type VARCHAR(128),
    file_size BIGINT,
    view_angle VARCHAR(64),
    metadata_json JSON,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY idx_media_owner (owner_type, owner_id, media_type)
);

CREATE TABLE evidence_item (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    evidence_type VARCHAR(64) NOT NULL,
    title VARCHAR(255) NOT NULL,
    content TEXT NOT NULL,
    source_type VARCHAR(64) NOT NULL,
    source_ref VARCHAR(512),
    confidence DECIMAL(5, 4),
    review_status VARCHAR(32) NOT NULL DEFAULT 'candidate',
    vehicle_instance_id BIGINT,
    system_id BIGINT,
    entity_type_id BIGINT,
    metadata_json JSON,
    created_by BIGINT,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    KEY idx_evidence_type_status (evidence_type, review_status),
    KEY idx_evidence_vehicle_system (vehicle_instance_id, system_id),
    KEY idx_evidence_entity_type (entity_type_id),
    CONSTRAINT fk_evidence_vehicle FOREIGN KEY (vehicle_instance_id) REFERENCES vehicle_instance(id),
    CONSTRAINT fk_evidence_system FOREIGN KEY (system_id) REFERENCES system_catalog(id),
    CONSTRAINT fk_evidence_entity_type FOREIGN KEY (entity_type_id) REFERENCES entity_type(id)
);

CREATE TABLE dataset_image (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    file_name VARCHAR(255) NOT NULL,
    source_type VARCHAR(64) NOT NULL DEFAULT 'manual_upload',
    vehicle_hint VARCHAR(255),
    system_id BIGINT,
    width INT,
    height INT,
    annotation_status VARCHAR(32) NOT NULL DEFAULT 'unlabeled',
    split VARCHAR(32) NOT NULL DEFAULT 'unassigned',
    object_count INT NOT NULL DEFAULT 0,
    quality_score DECIMAL(5, 4),
    image_data_url MEDIUMTEXT,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    KEY idx_dataset_image_status (annotation_status, split),
    KEY idx_dataset_image_system (system_id),
    CONSTRAINT fk_dataset_image_system FOREIGN KEY (system_id) REFERENCES system_catalog(id)
);

CREATE TABLE dataset_annotation (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    image_id BIGINT NOT NULL,
    entity_type_id BIGINT NOT NULL,
    bbox_json JSON NOT NULL,
    annotation_type VARCHAR(32) NOT NULL DEFAULT 'bbox',
    status VARCHAR(32) NOT NULL DEFAULT 'draft',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    KEY idx_annotation_image (image_id),
    KEY idx_annotation_entity (entity_type_id),
    CONSTRAINT fk_annotation_image FOREIGN KEY (image_id) REFERENCES dataset_image(id),
    CONSTRAINT fk_annotation_entity FOREIGN KEY (entity_type_id) REFERENCES entity_type(id)
);

CREATE TABLE vision_task (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    file_name VARCHAR(255) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'completed',
    detector_name VARCHAR(128) NOT NULL,
    object_count INT NOT NULL DEFAULT 0,
    ai_summary TEXT,
    metadata_json JSON,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY idx_vision_task_status (status, created_at)
);

CREATE TABLE collector_task (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    source VARCHAR(64) NOT NULL,
    target VARCHAR(255) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'planned',
    strategy VARCHAR(64) NOT NULL,
    fields_json JSON,
    notes_json JSON,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    KEY idx_collector_status (source, status)
);

CREATE TABLE user_account (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    username VARCHAR(64) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    display_name VARCHAR(128) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'active',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

CREATE TABLE role (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    code VARCHAR(64) NOT NULL UNIQUE,
    name VARCHAR(128) NOT NULL,
    data_scope VARCHAR(32) NOT NULL DEFAULT 'self',
    is_builtin BOOLEAN NOT NULL DEFAULT FALSE,
    status VARCHAR(32) NOT NULL DEFAULT 'active'
);

CREATE TABLE permission (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    code VARCHAR(128) NOT NULL UNIQUE,
    name VARCHAR(128) NOT NULL,
    resource_type VARCHAR(32) NOT NULL,
    action VARCHAR(32) NOT NULL
);

CREATE TABLE user_role (
    user_id BIGINT NOT NULL,
    role_id BIGINT NOT NULL,
    PRIMARY KEY (user_id, role_id),
    CONSTRAINT fk_user_role_user FOREIGN KEY (user_id) REFERENCES user_account(id),
    CONSTRAINT fk_user_role_role FOREIGN KEY (role_id) REFERENCES role(id)
);

CREATE TABLE role_permission (
    role_id BIGINT NOT NULL,
    permission_id BIGINT NOT NULL,
    PRIMARY KEY (role_id, permission_id),
    CONSTRAINT fk_role_permission_role FOREIGN KEY (role_id) REFERENCES role(id),
    CONSTRAINT fk_role_permission_permission FOREIGN KEY (permission_id) REFERENCES permission(id)
);

CREATE TABLE data_scope_rule (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    role_id BIGINT NOT NULL,
    resource_type VARCHAR(64) NOT NULL,
    scope_type VARCHAR(32) NOT NULL,
    scope_config_json JSON,
    CONSTRAINT fk_scope_role FOREIGN KEY (role_id) REFERENCES role(id)
);

CREATE TABLE audit_log (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    user_id BIGINT,
    action VARCHAR(64) NOT NULL,
    target_type VARCHAR(64) NOT NULL,
    target_id BIGINT,
    before_json JSON,
    after_json JSON,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    KEY idx_audit_target (target_type, target_id),
    CONSTRAINT fk_audit_user FOREIGN KEY (user_id) REFERENCES user_account(id)
);

CREATE TABLE import_job (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    job_name VARCHAR(255) NOT NULL,
    source_type VARCHAR(32) NOT NULL DEFAULT 'manual',
    status VARCHAR(32) NOT NULL DEFAULT 'pending',
    created_by BIGINT,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

CREATE TABLE import_job_item (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    job_id BIGINT NOT NULL,
    source_row_number INT NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'pending',
    raw_data JSON,
    error_message TEXT,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_import_item_job FOREIGN KEY (job_id) REFERENCES import_job(id)
);

CREATE TABLE export_job (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    job_name VARCHAR(255) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'pending',
    created_by BIGINT,
    file_url VARCHAR(1024),
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);
