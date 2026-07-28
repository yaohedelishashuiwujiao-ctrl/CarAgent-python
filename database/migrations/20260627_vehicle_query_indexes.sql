SET NAMES utf8mb4;

ALTER TABLE vehicle_instance
  ADD INDEX idx_vehicle_status_id (status, id),
  ADD INDEX idx_vehicle_source_status_id (source_type, status, id);

ALTER TABLE instance_attribute_value
  ADD INDEX idx_value_attribute_text_target (attribute_id, value_text(128), target_type, target_id);
