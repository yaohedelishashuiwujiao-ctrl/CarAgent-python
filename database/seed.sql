SET NAMES utf8mb4;

INSERT INTO entity_type (category, code, name, description, is_builtin, sort_order) VALUES
('vehicle', 'vehicle', '整车', '整车实体类型，用于创建小鹏 X9 等车型实例', TRUE, 10),
('component', 'upper_control_arm', '上控制臂/上摆臂', '悬架系统零部件实体类型，位置由实例属性表达', TRUE, 100),
('component', 'lower_control_arm', '下控制臂/下摆臂', '悬架系统零部件实体类型，位置由实例属性表达', TRUE, 105),
('component', 'front_subframe', '前副车架', '悬架系统零部件实体类型', TRUE, 110),
('component', 'brake_disc', '制动盘', '制动系统零部件实体类型', TRUE, 200),
('component', 'brake_caliper', '制动卡钳', '制动系统零部件实体类型', TRUE, 210),
('component', 'steering_knuckle', '转向节', '转向系统零部件实体类型', TRUE, 300),
('component', 'tie_rod', '转向拉杆', '转向系统零部件实体类型', TRUE, 310),
('component', 'drive_shaft', '半轴', '动力系统零部件实体类型', TRUE, 400);

INSERT INTO system_catalog (code, name, description, sort_order) VALUES
('suspension', '悬架系统', '虚拟系统节点，用于组织悬架零部件和系统级数据', 10),
('braking', '制动系统', '虚拟系统节点，用于组织制动零部件和系统级数据', 20),
('steering', '转向系统', '虚拟系统节点，用于组织转向零部件和系统级数据', 30),
('powertrain', '动力系统', '虚拟系统节点，用于组织动力零部件和系统级数据', 40);

INSERT INTO component_entity_system (entity_type_id, system_id)
SELECT et.id, sc.id FROM entity_type et JOIN system_catalog sc
WHERE (et.code = 'upper_control_arm' AND sc.code = 'suspension')
   OR (et.code = 'lower_control_arm' AND sc.code = 'suspension')
   OR (et.code = 'front_subframe' AND sc.code = 'suspension')
   OR (et.code = 'brake_disc' AND sc.code = 'braking')
   OR (et.code = 'brake_caliper' AND sc.code = 'braking')
   OR (et.code = 'steering_knuckle' AND sc.code = 'steering')
   OR (et.code = 'tie_rod' AND sc.code = 'steering')
   OR (et.code = 'drive_shaft' AND sc.code = 'powertrain');

INSERT INTO entity_attribute_group (entity_type_id, code, name, sort_order)
SELECT id, 'basic', '基本信息', 10 FROM entity_type WHERE code = 'vehicle';

INSERT INTO entity_attribute_group (entity_type_id, code, name, sort_order)
SELECT id, 'dimension', '尺寸参数', 20 FROM entity_type WHERE code = 'vehicle';

INSERT INTO entity_attribute_group (entity_type_id, code, name, sort_order)
SELECT id, 'basic', '零部件信息', 10 FROM entity_type WHERE category = 'component';

INSERT INTO entity_attribute_group (entity_type_id, code, name, sort_order)
SELECT id, 'material', '材料与工艺', 20 FROM entity_type WHERE category = 'component';

INSERT INTO entity_attribute (entity_type_id, group_id, code, name, attr_type, unit, is_required, is_searchable, sort_order)
SELECT et.id, g.id, 'brand', '品牌', 'text', NULL, TRUE, TRUE, 10
FROM entity_type et JOIN entity_attribute_group g ON g.entity_type_id = et.id AND g.code = 'basic'
WHERE et.code = 'vehicle';

INSERT INTO entity_attribute (entity_type_id, group_id, code, name, attr_type, unit, is_required, is_searchable, sort_order)
SELECT et.id, g.id, 'model_name', '车型名称', 'text', NULL, TRUE, TRUE, 20
FROM entity_type et JOIN entity_attribute_group g ON g.entity_type_id = et.id AND g.code = 'basic'
WHERE et.code = 'vehicle';

INSERT INTO entity_attribute (entity_type_id, group_id, code, name, attr_type, unit, is_required, is_searchable, sort_order)
SELECT et.id, g.id, 'wheelbase', '轴距', 'number', 'mm', FALSE, TRUE, 30
FROM entity_type et JOIN entity_attribute_group g ON g.entity_type_id = et.id AND g.code = 'dimension'
WHERE et.code = 'vehicle';

INSERT INTO entity_attribute (entity_type_id, group_id, code, name, attr_type, unit, is_required, is_searchable, sort_order)
SELECT et.id, g.id, 'curb_weight', '整备质量', 'number', 'kg', FALSE, TRUE, 40
FROM entity_type et JOIN entity_attribute_group g ON g.entity_type_id = et.id AND g.code = 'dimension'
WHERE et.code = 'vehicle';

INSERT INTO entity_attribute (entity_type_id, group_id, code, name, attr_type, unit, is_required, is_searchable, sort_order)
SELECT et.id, g.id, 'position', '安装位置', 'text', NULL, FALSE, TRUE, 10
FROM entity_type et JOIN entity_attribute_group g ON g.entity_type_id = et.id AND g.code = 'basic'
WHERE et.category = 'component';

INSERT INTO entity_attribute (entity_type_id, group_id, code, name, attr_type, unit, is_required, is_searchable, sort_order)
SELECT et.id, g.id, 'weight', '重量', 'number', 'kg', FALSE, TRUE, 20
FROM entity_type et JOIN entity_attribute_group g ON g.entity_type_id = et.id AND g.code = 'basic'
WHERE et.category = 'component';

INSERT INTO entity_attribute (entity_type_id, group_id, code, name, attr_type, unit, is_required, is_searchable, sort_order)
SELECT et.id, g.id, 'material', '材料', 'text', NULL, FALSE, TRUE, 30
FROM entity_type et JOIN entity_attribute_group g ON g.entity_type_id = et.id AND g.code = 'material'
WHERE et.category = 'component';

INSERT INTO entity_attribute (entity_type_id, group_id, code, name, attr_type, unit, is_required, is_searchable, sort_order)
SELECT et.id, g.id, 'manufacturing', '工艺', 'text', NULL, FALSE, FALSE, 40
FROM entity_type et JOIN entity_attribute_group g ON g.entity_type_id = et.id AND g.code = 'material'
WHERE et.category = 'component';

INSERT INTO vehicle_instance (entity_type_id, vehicle_code, vehicle_name, source_type)
SELECT id, 'XPENG_X9', '小鹏 X9', 'manual' FROM entity_type WHERE code = 'vehicle';

INSERT INTO vehicle_system_profile (vehicle_instance_id, system_id, profile_name, sort_order)
SELECT v.id, s.id, CONCAT(v.vehicle_name, ' / ', s.name), s.sort_order
FROM vehicle_instance v
JOIN system_catalog s
WHERE v.vehicle_code = 'XPENG_X9';

INSERT INTO component_instance (vehicle_instance_id, system_id, entity_type_id, component_code, component_name, source_type)
SELECT v.id, s.id, et.id, 'XPENG_X9_LF_UPPER_ARM', '小鹏 X9 左前上摆臂', 'manual'
FROM vehicle_instance v
JOIN system_catalog s ON s.code = 'suspension'
JOIN entity_type et ON et.code = 'upper_control_arm'
WHERE v.vehicle_code = 'XPENG_X9';

INSERT INTO instance_attribute_value (target_type, target_id, attribute_id, value_text, value_source)
SELECT 'vehicle', v.id, a.id, '小鹏', 'manual'
FROM vehicle_instance v
JOIN entity_attribute a ON a.code = 'brand'
JOIN entity_type et ON et.id = a.entity_type_id AND et.code = 'vehicle'
WHERE v.vehicle_code = 'XPENG_X9';

INSERT INTO instance_attribute_value (target_type, target_id, attribute_id, value_text, value_source)
SELECT 'vehicle', v.id, a.id, 'X9', 'manual'
FROM vehicle_instance v
JOIN entity_attribute a ON a.code = 'model_name'
JOIN entity_type et ON et.id = a.entity_type_id AND et.code = 'vehicle'
WHERE v.vehicle_code = 'XPENG_X9';

INSERT INTO instance_attribute_value (target_type, target_id, attribute_id, value_number, unit, value_source)
SELECT 'vehicle', v.id, a.id, 3160, 'mm', 'manual'
FROM vehicle_instance v
JOIN entity_attribute a ON a.code = 'wheelbase'
JOIN entity_type et ON et.id = a.entity_type_id AND et.code = 'vehicle'
WHERE v.vehicle_code = 'XPENG_X9';

INSERT INTO instance_attribute_value (target_type, target_id, attribute_id, value_text, value_source)
SELECT 'component', c.id, a.id, '左前', 'manual'
FROM component_instance c
JOIN entity_attribute a ON a.code = 'position'
JOIN entity_type et ON et.id = a.entity_type_id AND et.code = 'upper_control_arm'
WHERE c.component_code = 'XPENG_X9_LF_UPPER_ARM';

INSERT INTO instance_attribute_value (target_type, target_id, attribute_id, value_number, unit, value_source)
SELECT 'component', c.id, a.id, 2.8, 'kg', 'manual'
FROM component_instance c
JOIN entity_attribute a ON a.code = 'weight'
JOIN entity_type et ON et.id = a.entity_type_id AND et.code = 'upper_control_arm'
WHERE c.component_code = 'XPENG_X9_LF_UPPER_ARM';

INSERT INTO instance_attribute_value (target_type, target_id, attribute_id, value_text, value_source)
SELECT 'component', c.id, a.id, '铝合金', 'manual'
FROM component_instance c
JOIN entity_attribute a ON a.code = 'material'
JOIN entity_type et ON et.id = a.entity_type_id AND et.code = 'upper_control_arm'
WHERE c.component_code = 'XPENG_X9_LF_UPPER_ARM';

INSERT INTO evidence_item
(evidence_type, title, content, source_type, source_ref, confidence, review_status,
 vehicle_instance_id, system_id, entity_type_id, metadata_json)
SELECT
    'manual_research',
    '小鹏 X9 左前上摆臂轻量化线索',
    '人工维护线索：左前上摆臂已记录材料为铝合金，重量为 2.8kg，可作为悬架轻量化分析的已审核证据之一。',
    'manual',
    'seed:xpeng_x9_lf_upper_arm',
    0.92,
    'reviewed',
    v.id,
    s.id,
    et.id,
    JSON_OBJECT('component_code', c.component_code, 'attribute_codes', JSON_ARRAY('material', 'weight'))
FROM vehicle_instance v
JOIN system_catalog s ON s.code = 'suspension'
JOIN entity_type et ON et.code = 'upper_control_arm'
JOIN component_instance c ON c.vehicle_instance_id = v.id AND c.entity_type_id = et.id
WHERE v.vehicle_code = 'XPENG_X9';

INSERT INTO dataset_image
(file_name, source_type, vehicle_hint, system_id, width, height, annotation_status, split, object_count, quality_score, created_at)
SELECT 'xpeng_x9_front_suspension_001.jpg', 'manual_upload', '小鹏 X9', id, 1600, 1000, 'reviewed', 'train', 3, 0.86, '2026-06-24 10:00:00'
FROM system_catalog WHERE code = 'suspension';

INSERT INTO dataset_image
(file_name, source_type, vehicle_hint, system_id, width, height, annotation_status, split, object_count, quality_score, created_at)
SELECT 'brake_disc_closeup_001.jpg', 'web_research', NULL, id, 1280, 960, 'labeling', 'val', 1, 0.74, '2026-06-24 10:20:00'
FROM system_catalog WHERE code = 'braking';

INSERT INTO dataset_image
(file_name, source_type, vehicle_hint, system_id, width, height, annotation_status, split, object_count, quality_score, created_at)
SELECT 'rear_powertrain_underbody_001.jpg', 'video_frame', NULL, id, 1920, 1080, 'unlabeled', 'unassigned', 0, 0.68, '2026-06-24 10:40:00'
FROM system_catalog WHERE code = 'powertrain';

INSERT INTO dataset_annotation (image_id, entity_type_id, bbox_json, annotation_type, status, created_at)
SELECT img.id, et.id, JSON_ARRAY(250, 420, 640, 620), 'bbox', 'reviewed', '2026-06-24 10:05:00'
FROM dataset_image img
JOIN entity_type et ON et.code = 'upper_control_arm'
WHERE img.file_name = 'xpeng_x9_front_suspension_001.jpg';

INSERT INTO collector_task (source, target, status, strategy, fields_json, notes_json)
VALUES (
    'autohome',
    '小鹏 X9',
    'planned',
    'vehicle_profile',
    JSON_ARRAY('车型基本参数', '车身尺寸', '底盘/转向/制动参数', '图片链接', '页面来源 URL'),
    JSON_ARRAY('采集结果先进入 evidence，不直接写入正式属性。', '正式使用前需要处理来源授权、频率限制和页面结构变化。', '字段入库前必须经过人工复核。')
);

INSERT INTO permission (code, name, resource_type, action) VALUES
('dashboard:read', '查看首页', 'dashboard', 'read'),
('metadata:read', '查看元数据', 'metadata', 'read'),
('metadata:update', '维护元数据', 'metadata', 'update'),
('asset:create', '创建实例数据', 'asset', 'create'),
('asset:read', '查看实例数据', 'asset', 'read'),
('asset:update', '维护实例数据', 'asset', 'update'),
('asset:delete', '删除实例数据', 'asset', 'delete'),
('asset:import', '导入实例数据', 'asset', 'import'),
('asset:export', '导出实例数据', 'asset', 'export'),
('agent:analyze', '使用 AI 分析', 'agent', 'analyze'),
('permission:manage', '管理权限', 'permission', 'manage');

INSERT INTO role (code, name, data_scope, is_builtin) VALUES
('super_admin', '超级管理员', 'all', TRUE),
('admin', '管理员', 'all', TRUE),
('data_maintainer', '数据维护员', 'department', TRUE),
('analyst', '分析师', 'department', TRUE),
('viewer', '只读用户', 'self', TRUE);

INSERT INTO role_permission (role_id, permission_id)
SELECT r.id, p.id FROM role r JOIN permission p
WHERE r.code IN ('super_admin', 'admin');

INSERT INTO role_permission (role_id, permission_id)
SELECT r.id, p.id FROM role r JOIN permission p
WHERE r.code = 'data_maintainer'
  AND p.code IN ('dashboard:read', 'metadata:read', 'asset:create', 'asset:read', 'asset:update', 'asset:import');

INSERT INTO role_permission (role_id, permission_id)
SELECT r.id, p.id FROM role r JOIN permission p
WHERE r.code = 'analyst'
  AND p.code IN ('dashboard:read', 'asset:read', 'asset:export', 'agent:analyze');

INSERT INTO role_permission (role_id, permission_id)
SELECT r.id, p.id FROM role r JOIN permission p
WHERE r.code = 'viewer'
  AND p.code IN ('dashboard:read', 'asset:read');
