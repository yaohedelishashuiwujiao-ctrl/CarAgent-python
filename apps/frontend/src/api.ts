export type EntityType = {
  id: number;
  category: "vehicle" | "component";
  code: string;
  name: string;
  description?: string;
  is_builtin: boolean;
  default_system_id?: number;
};

export type SystemCatalog = {
  id: number;
  code: string;
  name: string;
  description?: string;
  is_builtin: boolean;
};

export type Attribute = {
  id: number;
  entity_type_id: number;
  group_id?: number;
  code: string;
  name: string;
  attr_type: string;
  unit?: string;
  is_required: boolean;
  is_searchable: boolean;
  is_importable: boolean;
  is_exportable: boolean;
};

export type InstanceValue = {
  attribute_id: number;
  attribute_code: string;
  value: unknown;
  unit?: string;
  source: string;
  confidence?: number;
};

export type VehicleInstance = {
  id: number;
  entity_type_id: number;
  vehicle_code: string;
  vehicle_name: string;
  source_type: string;
  status: string;
  values: InstanceValue[];
};

export type VehicleListResponse = {
  items: VehicleInstance[];
  total: number;
  page: number;
  page_size: number;
  source_counts: Record<string, number>;
};

export type VehicleSeriesSummary = {
  series_id: string;
  series_name: string;
  source_type: string;
  spec_count: number;
};

export type AttributeValueDetail = {
  attribute_id: number;
  attribute_code: string;
  attribute_name: string;
  attr_type: string;
  unit?: string | null;
  value: unknown;
  source: string;
  confidence?: number | null;
};

export type AttributeValueGroup = {
  group_id?: number | null;
  group_code: string;
  group_name: string;
  values: AttributeValueDetail[];
};

export type VehicleDetailResponse = {
  vehicle: VehicleInstance;
  groups: AttributeValueGroup[];
};

export type ComponentInstance = {
  id: number;
  vehicle_instance_id: number;
  system_id: number;
  entity_type_id: number;
  component_code: string;
  component_name: string;
  source_type: string;
  status: string;
  values: InstanceValue[];
};

export type AssetTreeNode = {
  id: number;
  node_type: "vehicle_series" | "vehicle" | "system_profile" | "component";
  title: string;
  code: string;
  entity_type_id?: number;
  system_id?: number;
  instance_id?: number;
  children?: AssetTreeNode[];
};

export type Role = {
  code: string;
  name: string;
  data_scope: string;
  permissions: string[];
};

export type VisionDetection = {
  id: number;
  entity_type_id?: number;
  entity_type_code: string;
  label: string;
  system_id?: number;
  system_name?: string;
  confidence: number;
  bbox: number[];
  polygon?: number[] | null;
  source: string;
  review_status: string;
  reasoning: string;
};

export type VisionTask = {
  id: number;
  file_name: string;
  status: string;
  detector_name: string;
  object_count: number;
  ai_summary: string;
};

export type VisionAnalyzeRequest = {
  file_name: string;
  image_data_url: string;
  confidence?: number;
  iou?: number;
  image_size?: number;
  vehicle_instance_id?: number;
  note?: string;
};

export type VisionAnalyzeResponse = {
  task: VisionTask;
  image: { width: number; height: number };
  detections: VisionDetection[];
  annotated_image: string;
  ai_summary: string;
};

export type VisionRefineRequest = {
  file_name: string;
  image_data_url: string;
  bbox: number[];
  iterations?: number;
};

export type VisionRefineResponse = {
  file_name: string;
  bbox: number[];
  polygon: number[];
  annotated_image: string;
  mask_coverage: number;
  ai_summary: string;
};

export type DatasetImage = {
  id: number;
  file_name: string;
  source_type: string;
  vehicle_hint?: string;
  system_id?: number;
  width?: number;
  height?: number;
  annotation_status: string;
  split: string;
  object_count: number;
  quality_score?: number;
  created_at: string;
  image_data_url?: string;
};

export type DatasetClassStat = {
  entity_type_id: number;
  entity_type_code: string;
  entity_type_name: string;
  system_name?: string;
  labeled_instances: number;
  target_instances: number;
};

export type DatasetSummary = {
  image_count: number;
  unlabeled_count: number;
  labeling_count: number;
  reviewed_count: number;
  train_count: number;
  val_count: number;
  test_count: number;
  class_stats: DatasetClassStat[];
};

export type DatasetAnnotation = {
  id: number;
  image_id: number;
  entity_type_id: number;
  entity_type_code: string;
  entity_type_name: string;
  bbox: number[];
  annotation_type: string;
  status: string;
  created_at: string;
};

export type YoloExportPlan = {
  export_name: string;
  format: string;
  class_count: number;
  image_count: number;
  train_count: number;
  val_count: number;
  test_count: number;
  classes: string[];
  notes: string[];
};

export type AgentSource = {
  id: string;
  title: string;
  source_type: string;
  evidence_type?: string;
  score: number;
  content: string;
  confidence?: number;
  review_status?: string;
  metadata: Record<string, unknown>;
};

export type EvidenceItem = {
  id: string;
  title: string;
  content: string;
  evidence_type: string;
  source_type: string;
  source_ref?: string;
  confidence?: number;
  review_status: string;
  vehicle_instance_id?: number;
  system_id?: number;
  entity_type_id?: number;
  metadata: Record<string, unknown>;
  created_at?: string;
};

export type EvidenceSummary = {
  total_count: number;
  reviewed_count: number;
  candidate_count: number;
  rejected_count: number;
  low_confidence_count: number;
  source_counts: Record<string, number>;
  type_counts: Record<string, number>;
};

export type RuntimeCapability = {
  key: string;
  name: string;
  status: "ready" | "missing" | "demo" | "degraded";
  level: string;
  detail: string;
};

export type RuntimeStatus = {
  service: string;
  environment: string;
  capabilities: RuntimeCapability[];
  warnings: string[];
  vision_backend: Record<string, string>;
};

export type KnowledgeMetric = {
  label: string;
  value: string;
  hint?: string | null;
};

export type KnowledgeStage = {
  key: string;
  name: string;
  status: "ready" | "pending" | "partial" | "degraded" | string;
  summary: string;
  metrics: KnowledgeMetric[];
  notes: string[];
};

export type KnowledgeSampleRow = {
  id: string;
  brand: string;
  model: string;
  year: string;
  source_type: string;
  official_url: string;
  selector_hint?: string | null;
  market?: string | null;
  language?: string | null;
  status?: string | null;
  artifact_path?: string | null;
  text_path?: string | null;
  bytes?: number | null;
  final_url?: string | null;
  downloaded_at?: string | null;
  parent_id?: string | null;
};

export type KnowledgeVersion = {
  id: string;
  name: string;
  state: string;
  detail: string;
};

export type KnowledgeWorkspaceStatus = {
  generated_at: string;
  snapshot_name: string;
  metrics: KnowledgeMetric[];
  stages: KnowledgeStage[];
  source_samples: KnowledgeSampleRow[];
  artifact_samples: KnowledgeSampleRow[];
  versions: KnowledgeVersion[];
  notes: string[];
};

export type KnowledgeSearchHit = {
  id: string;
  brand: string;
  model: string;
  year: string;
  source_type: string;
  official_url: string;
  artifact_path: string;
  text_path?: string | null;
  score: number;
  title: string;
  excerpt: string;
  matched_terms: string[];
};

export type KnowledgeSearchResponse = {
  query: string;
  top_k: number;
  total_matches: number;
  hits: KnowledgeSearchHit[];
};

export type AutohomeFieldProfile = {
  attribute_code: string;
  title_id: string;
  group: string;
  field_name: string;
  attr_type: string;
  unit?: string | null;
  sample_values: string[];
  non_empty_count: number;
};

export type AutohomeScanResponse = {
  source_dir: string;
  long_csv_path: string;
  status: string;
  series_count: number;
  spec_count: number;
  field_count: number;
  row_count: number;
  groups: string[];
  fields: AutohomeFieldProfile[];
  notes: string[];
};

export type AutohomeImportResponse = {
  source_dir: string;
  status: string;
  dry_run: boolean;
  series_count: number;
  spec_count: number;
  field_count: number;
  vehicle_created: number;
  vehicle_updated: number;
  attribute_created: number;
  attribute_reused: number;
  value_inserted: number;
  skipped_values: number;
  notes: string[];
};

export function apiBaseUrl(): string {
  return import.meta.env.VITE_API_BASE_URL || "";
}

export function apiUrl(path: string): string {
  if (/^https?:\/\//.test(path)) {
    return path;
  }
  const baseUrl = apiBaseUrl().replace(/\/$/, "");
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  return baseUrl ? `${baseUrl}${normalizedPath}` : normalizedPath;
}

export function apiHeaders(json = false): Record<string, string> {
  const headers: Record<string, string> = {};
  if (json) headers["Content-Type"] = "application/json";
  const stored = typeof window !== "undefined" ? window.localStorage.getItem("subjects_access_token") : null;
  const token = stored || import.meta.env.VITE_API_ACCESS_TOKEN || "";
  if (token) headers.Authorization = `Bearer ${token}`;
  return headers;
}

export async function fetchJson<T>(url: string): Promise<T> {
  const response = await fetch(apiUrl(url), { headers: apiHeaders() });
  if (!response.ok) {
    throw new Error(`Request failed: ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export async function postJson<T>(url: string, body: unknown): Promise<T> {
  const response = await fetch(apiUrl(url), {
    method: "POST",
    headers: apiHeaders(true),
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail || `Request failed: ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export async function putJson<T>(url: string, body: unknown): Promise<T> {
  const response = await fetch(apiUrl(url), {
    method: "PUT",
    headers: apiHeaders(true),
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail || `Request failed: ${response.status}`);
  }
  return response.json() as Promise<T>;
}
