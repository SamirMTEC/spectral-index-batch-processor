const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";
const desktopLocal = import.meta.env.VITE_DESKTOP_LOCAL === "true";
const externalAuthConfigured = Boolean(
  import.meta.env.VITE_AUTH0_DOMAIN &&
  import.meta.env.VITE_AUTH0_CLIENT_ID &&
  import.meta.env.VITE_AUTH0_AUDIENCE,
);

type AccessTokenProvider = () => Promise<string>;
let accessTokenProvider: AccessTokenProvider | null = null;

export function configureAccessTokenProvider(
  provider: AccessTokenProvider | null,
) {
  accessTokenProvider = provider;
}

export type ProjectStatus = "active" | "archived";
export type RadiometryMode = "unknown" | "digital_number" | "reflectance";

export interface Project {
  id: string;
  name: string;
  description: string | null;
  status: ProjectStatus;
  archived_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface Dataset {
  id: string;
  project_id: string;
  band_sequence_profile_id: string | null;
  name: string;
  description: string | null;
  crop: string | null;
  experiment: string | null;
  location: string | null;
  sensor: string | null;
  radiometry_mode: RadiometryMode;
  notes: string | null;
  state: string;
  created_at: string;
  updated_at: string;
}

export interface BandSequenceProfile {
  id: string;
  name: string;
  filename_prefix: string;
  capture_prefix: string;
  band_order: string[];
  created_at: string;
  updated_at: string;
}

export interface BandSequenceProfileInput {
  name: string;
  filename_prefix: string;
  capture_prefix: string;
  band_order: string[];
}

export interface Dashboard {
  project_count: number;
  dataset_count: number;
  recent_projects: Project[];
}

export interface ProjectInput {
  name: string;
  description?: string | undefined;
}

export interface DatasetInput {
  name: string;
  description?: string | undefined;
  crop?: string | undefined;
  experiment?: string | undefined;
  location?: string | undefined;
  sensor?: string | undefined;
  radiometry_mode: RadiometryMode;
  notes?: string | undefined;
}

export type UploadMode = "files" | "directory" | "zip";
export type UploadStatus =
  | "pending"
  | "uploading"
  | "uploaded"
  | "validating"
  | "valid"
  | "invalid"
  | "failed";

export interface UploadObject {
  id: string;
  upload_session_id: string;
  filename: string;
  client_relative_path: string;
  extension: string;
  declared_size: number;
  observed_size: number | null;
  role: "scientific" | "auxiliary" | "archive";
  state: UploadStatus;
  client_checksum: string | null;
  verified_checksum: string | null;
  failure_code: string | null;
  failure_message: string | null;
  metadata_json: Record<string, unknown>;
  method?: "single" | "multipart" | null;
  upload_url?: string | null;
  part_size?: number | null;
  part_count?: number | null;
}

export interface UploadSession {
  id: string;
  dataset_id: string;
  mode: UploadMode;
  state: "pending" | "uploading" | "completed" | "failed" | "cancelled";
  total_files: number;
  total_bytes: number;
  objects: UploadObject[];
}

export type AlignmentStatus =
  "unknown" | "compatible" | "requires_transform" | "incompatible";
export type ROIGeometryType = "rectangle" | "polygon";

export interface CalibrationBand {
  id: string;
  band: string;
  filename: string;
  width: number;
  height: number;
  dtype: string;
  count: number;
  sha256: string;
  preview_url: string | null;
  p4_signal_available: boolean;
  metadata: Record<string, unknown>;
}

export interface CalibrationPanel {
  id: string;
  name: string;
  manufacturer: string | null;
  model: string | null;
  serial_number: string | null;
  calibrated_at: string | null;
  notes: string | null;
  values: Record<string, Record<string, number>>;
  source: { kind: string; label: string; certified: boolean };
  certificate_sha256: string | null;
}

export interface CalibrationROI {
  surface: string;
  band: string | null;
  coordinates: [number, number][];
  role: "fit" | "validation";
}

export interface CalibrationRunInput {
  dataset_id: string;
  capture_id: string;
  target_capture_ids?: string[];
  additional_references?: { capture_id: string; rois: CalibrationROI[] }[];
  target_reference_map?: Record<string, string>;
  reference_combination?: "per_target" | "mean_coefficients";
  session_name?: string | null;
  session_date?: string | null;
  session_timezone?: string | null;
  illumination_method?: "stable_panel" | "not_assessed";
  illumination_notes?: string | null;
  panel_id: string;
  method: "proportional" | "linear";
  input_mode: "p4_signal" | "dn_empirical";
  rois: CalibrationROI[];
  reference_band: string;
  selected_band?: string | null;
  acknowledge_dn_limits: boolean;
  exclude_saturated: boolean;
  edge_inset_percent: number;
  weighted: boolean;
  robust: boolean;
  max_cv: number | null;
  max_saturation_percent: number | null;
  max_validation_rmse: number | null;
  execute: boolean;
}

export interface CalibrationRun {
  id: string;
  state: "draft" | "queued" | "running" | "cancel_requested" | "cancelled" | "completed" | "failed";
  method: string;
  input_mode: string;
  config: CalibrationRunInput;
  result: {
    targets?: {
      capture_id: string;
      capture_key: string;
      reference_capture_id?: string;
      reference_capture_ids?: string[];
      state: "completed" | "failed";
      errors: string[];
      bands: Record<string, {
        output: string;
        original_filename: string;
        distribution: { invalid_percent: number; saturated_percent: number; out_of_range_percent: number };
      }>;
    }[];
    target_summary?: { requested: number; completed: number; failed: number; skipped?: number };
    reference_captures?: {
      capture_id: string;
      capture_key: string;
      bands: Record<string, { model: { slope: number; intercept: number }; output?: string; filename?: string; surfaces: { surface: string; role: string; reference: number }[] }>;
      output_directory: string;
    }[];
    bands: Record<string, {
      model: { slope: number; intercept: number; r2: number | null; rmse: number; residuals: number[] };
      fit_error: { rmse: number; mae: number; bias: number; r2: number | null };
      validation: { rmse: number; mae: number; bias: number } | null;
      internal_check?: { rmse: number; mae: number; bias: number } | null;
      distribution: {
        invalid_percent: number;
        saturated_percent: number;
        out_of_range_percent: number;
        histogram_before: { counts: number[]; edges: number[] };
        histogram_after: { counts: number[]; edges: number[] };
      };
      surfaces: { surface: string; role: string; reference: number; estimated: number; residual: number; dn: { median: number; valid_pixels: number; saturated_percent: number }; signal: { median: number; cv: number | null } }[];
    }>;
    warnings: string[];
    preview_urls?: Record<string, { original?: string; calibrated?: string }>;
    capture_preview_urls?: Record<string, Record<string, { original?: string; calibrated?: string }>>;
    combined_models?: Record<string, { slope: number; intercept: number; reference_capture_ids: string[] }>;
    combined_reference_check?: Record<string, { rmse: number; observations: { capture_id: string; capture_key: string; reference: number; estimated: number; residual: number }[] }>;
    reference_combination?: "mean_coefficients";
    classification: string;
    classification_reasons: string[];
  } | null;
  error: string | null;
  download_available: boolean;
  created_at: string;
}

export interface CaptureSummary {
  id: string;
  dataset_id: string;
  replicate_id: string | null;
  replicate_label: string | null;
  capture_key: string;
  band_count: number;
  band_codes: string[];
  roi_count: number;
  created_at: string;
}

export interface CaptureBand {
  id: string;
  band_code: string;
  filename: string;
  width: number;
  height: number;
  crs: string | null;
  transform: number[] | null;
  preview_url: string | null;
  approximate_valid_fraction: number | null;
  compatible: boolean;
  compatibility_error: string | null;
}

export interface CaptureDetail {
  id: string;
  dataset_id: string;
  replicate_id: string | null;
  replicate_label: string | null;
  capture_key: string;
  alignment_status: AlignmentStatus;
  bands: CaptureBand[];
  created_at: string;
  updated_at: string;
}

export interface ROIVersion {
  id: string;
  version_number: number;
  geometry_type: ROIGeometryType;
  normalized_coordinates: [number, number][];
  pixel_coordinates: [number, number][];
  reference_capture_id: string;
  reference_asset_id: string;
  reference_width: number;
  reference_height: number;
  crs: string | null;
  transform: number[] | null;
  spatial_coordinates: [number, number][] | null;
  geometry_checksum: string;
  area_pixels: number;
  approximate_valid_pixels: number | null;
  created_at: string;
}

export interface ROI {
  id: string;
  dataset_id: string;
  replicate_id: string | null;
  capture_id: string;
  name: string;
  description: string | null;
  state: "active" | "archived";
  current_version: ROIVersion;
  created_at: string;
  updated_at: string;
}

export interface ROIInput {
  name: string;
  description?: string;
  geometry_type: ROIGeometryType;
  normalized_coordinates: [number, number][];
  reference_asset_id: string;
  expected_version?: number;
}

export type ProcessingJobState =
  | "queued"
  | "running"
  | "completed"
  | "completed_with_errors"
  | "failed"
  | "cancelled";
export type ProcessingItemState =
  "pending" | "running" | "completed" | "skipped" | "failed";
export type SpectralIndexName = string;
export interface SpectralIndexDefinition {
  index: SpectralIndexName;
  formula_version: string;
  formula: string;
  required_bands: string[];
  category: string;
  description: string;
  approximate: boolean;
  unit: string;
  expected_range: number[] | null;
  aliases: string[];
  allowed_parameters: string[];
  default_parameters: Record<string, number>;
  scientific_note: string | null;
}
export type AnalysisScope = "roi" | "full_image";

export interface ProcessingStatistics {
  count: number;
  mean: number | null;
  median: number | null;
  min: number | null;
  max: number | null;
  std: number | null;
  p05: number | null;
  p25: number | null;
  p75: number | null;
  p95: number | null;
}

export interface ProcessingItem {
  id: string;
  capture_id: string | null;
  capture: string | null;
  index_name: string;
  analysis_scope: AnalysisScope;
  formula_version: string;
  state: ProcessingItemState;
  attempts: number;
  max_attempts: number;
  started_at: string | null;
  finished_at: string | null;
  error_code: string | null;
  message: string | null;
  file: string | null;
  result_available: boolean;
  statistics: ProcessingStatistics | null;
}

export interface ProcessingJob {
  id: string;
  dataset_id: string;
  parent_job_id: string | null;
  recipe_id: string | null;
  recipe_name: string | null;
  snapshot_checksum: string;
  software_version: string;
  state: ProcessingJobState;
  indices: string[];
  total_items: number;
  processed_items: number;
  completed_items: number;
  failed_items: number;
  skipped_items: number;
  progress: number;
  started_at: string | null;
  finished_at: string | null;
  elapsed_seconds: number;
  cancel_requested_at: string | null;
  warnings: string[];
  error_code: string | null;
  message: string | null;
  created_at: string;
  updated_at: string;
  items: ProcessingItem[];
}

export interface ResultsOverview {
  dataset_id: string;
  dataset_name: string;
  treatment_count: number;
  repetition_count: number;
  image_count: number;
  indices: string[];
  analyzed_pixels: number;
  analyzed_pixels_roi: number;
  analyzed_pixels_full_image: number;
  error_count: number;
  analyzed_at: string | null;
}

export interface ResultTableRow extends ProcessingStatistics {
  result_id: string;
  item_id: string;
  treatment: string | null;
  repetition: string | null;
  capture: string | null;
  index_name: string;
  analysis_scope: AnalysisScope;
  valid_pixels: number;
  original_preview_url: string | null;
  original_label: string | null;
}

export interface TreatmentSummary {
  treatment: string | null;
  index_name: string;
  analysis_scope: AnalysisScope;
  n: number;
  mean: number | null;
  std: number | null;
}

export interface ResultImage {
  result_id: string;
  item_id: string;
  treatment: string | null;
  repetition: string | null;
  capture: string | null;
  index_name: string;
  analysis_scope: AnalysisScope;
  original_preview_url: string | null;
  original_label: string | null;
}

export interface ProcessingResultError {
  item_id: string;
  file: string | null;
  repetition: string | null;
  problem: string;
  error_code: string | null;
  analysis_scope: AnalysisScope;
  recommended_action: string;
}

export interface ResultFile {
  id: string;
  item_id: string | null;
  artifact_type: string;
  state: string;
  filename: string | null;
  size_bytes: number | null;
  checksum_sha256: string | null;
  download_url: string | null;
  created_at: string;
}

export type ExportFormat =
  | "csv"
  | "xlsx"
  | "summary_csv"
  | "summary_xlsx"
  | "rois"
  | "manifest"
  | "logs"
  | "rasters_roi"
  | "rasters_full_image"
  | "calibrated_rasters"
  | "complete";

export interface ReproducibilityInput {
  item_id: string;
  capture: string | null;
  repetition: string | null;
  index_name: string;
  analysis_scope: AnalysisScope;
  formula_version: string;
  formula: string;
  parameters: Record<string, unknown>;
  roi_version_id: string | null;
  source_bands: {
    band_code: string | null;
    filename: string | null;
    sha256: string | null;
    object_version_id: string | null;
  }[];
  result_checksum_sha256: string | null;
  result_object_version_id: string | null;
}

export interface ProcessingResults {
  overview: ResultsOverview;
  table: {
    rows: ResultTableRow[];
    total: number;
    treatments: string[];
    indices: string[];
    scopes: AnalysisScope[];
  };
  treatment_summary: TreatmentSummary[];
  images: ResultImage[];
  errors: ProcessingResultError[];
  files: ResultFile[];
  reproducibility: {
    job_id: string;
    job_state: string;
    created_at: string;
    completed_at: string | null;
    parent_job_id: string | null;
    recipe_id: string | null;
    recipe_name: string | null;
    software_version: string;
    radiometry_mode: string;
    snapshot_schema_version: string;
    snapshot_checksum: string;
    formulas: {
      index: string;
      formula_version: string;
      formula: string;
      required_bands: string[];
      parameters: Record<string, number>;
      category?: string | null;
      description?: string | null;
      approximate?: boolean;
      unit?: string | null;
      expected_range?: number[] | null;
      aliases?: string[];
      scientific_note?: string | null;
    }[];
    captures: {
      capture_id: string;
      capture: string;
      repetition: string | null;
      treatment: string | null;
      roi: {
        name: string;
        geometry_type: string;
        area_pixels: number;
        geometry_checksum: string;
        reference_width: number;
        reference_height: number;
      } | null;
      band_mappings: {
        band: string;
        asset_id: string;
        filename: string;
        checksum_sha256: string | null;
        object_version_id: string | null;
      }[];
    }[];
    configuration: Record<string, unknown>;
    inputs: ReproducibilityInput[];
  };
}

export interface ProcessingRecipe {
  id: string;
  name: string;
  description: string | null;
  schema_version: string;
  definition_checksum: string;
  indices: {
    index: SpectralIndexName;
    formula_version: string;
    formula: string;
    required_bands: string[];
    parameters: Record<string, number>;
    category?: string | null;
    description?: string | null;
    approximate?: boolean;
    unit?: string | null;
    expected_range?: number[] | null;
    aliases?: string[];
    scientific_note?: string | null;
  }[];
  statistics: string[];
  created_at: string;
  updated_at: string;
}

async function developmentUserId() {
  if (desktopLocal) {
    const response = await fetch("/desktop-identity");
    if (!response.ok) throw new Error("A identidade local não está disponível.");
    const body = (await response.json()) as { userId: string };
    return body.userId;
  }
  const storageKey = "spectral-index-dev-user-id";
  const existing = window.localStorage.getItem(storageKey);
  if (existing) return existing;
  const generated = window.crypto.randomUUID();
  window.localStorage.setItem(storageKey, generated);
  return generated;
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  headers.set("Content-Type", "application/json");
  if (accessTokenProvider !== null) {
    headers.set("Authorization", `Bearer ${await accessTokenProvider()}`);
  } else if (externalAuthConfigured || (import.meta.env.PROD && !desktopLocal)) {
    throw new Error("A autenticação ainda não está pronta. Entre novamente.");
  } else {
    headers.set("X-Dev-User-Id", await developmentUserId());
  }
  const response = await fetch(`${apiBaseUrl}/api/v1${path}`, {
    ...options,
    headers,
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as {
      detail?: string | { msg?: string; loc?: unknown[] }[];
    } | null;
    const detail = body?.detail;
    const description = typeof detail === "string"
      ? detail
      : Array.isArray(detail)
        ? detail.map((item) => `${item.loc?.slice(1).join(".") ?? "campo"}: ${item.msg ?? "valor inválido"}`).join("; ")
        : `Request failed (${String(response.status)})`;
    throw new Error(description);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

async function requestBlob(path: string): Promise<string> {
  const headers = new Headers();
  if (accessTokenProvider !== null) {
    headers.set("Authorization", `Bearer ${await accessTokenProvider()}`);
  } else if (externalAuthConfigured || (import.meta.env.PROD && !desktopLocal)) {
    throw new Error("A autenticação ainda não está pronta. Entre novamente.");
  } else {
    headers.set("X-Dev-User-Id", await developmentUserId());
  }
  const response = await fetch(`${apiBaseUrl}/api/v1${path}`, {
    headers,
  });
  if (!response.ok) {
    throw new Error(
      `Não foi possível carregar a visualização (${String(response.status)})`,
    );
  }
  return URL.createObjectURL(await response.blob());
}

async function requestDownloadBlob(path: string): Promise<string> {
  const headers = new Headers();
  if (accessTokenProvider !== null) {
    headers.set("Authorization", `Bearer ${await accessTokenProvider()}`);
  } else if (externalAuthConfigured || (import.meta.env.PROD && !desktopLocal)) {
    throw new Error("A autenticação ainda não está pronta. Entre novamente.");
  } else {
    headers.set("X-Dev-User-Id", await developmentUserId());
  }
  const response = await fetch(`${apiBaseUrl}/api/v1${path}`, { headers });
  if (!response.ok) throw new Error(`Não foi possível baixar o pacote (${String(response.status)}).`);
  return URL.createObjectURL(await response.blob());
}

export const api = {
  getDashboard: () => request<Dashboard>("/dashboard"),
  listProjects: () => request<Project[]>("/projects"),
  getProject: (projectId: string) => request<Project>(`/projects/${projectId}`),
  createProject: (input: ProjectInput) =>
    request<Project>("/projects", {
      method: "POST",
      body: JSON.stringify(input),
    }),
  updateProject: (projectId: string, input: ProjectInput) =>
    request<Project>(`/projects/${projectId}`, {
      method: "PATCH",
      body: JSON.stringify(input),
    }),
  archiveProject: (projectId: string) =>
    request<Project>(`/projects/${projectId}/archive`, { method: "POST" }),
  listDatasets: (projectId: string) =>
    request<Dataset[]>(`/projects/${projectId}/datasets`),
  createDataset: (projectId: string, input: DatasetInput) =>
    request<Dataset>(`/projects/${projectId}/datasets`, {
      method: "POST",
      body: JSON.stringify(input),
    }),
  getDataset: (datasetId: string) => request<Dataset>(`/datasets/${datasetId}`),
  listBandSequenceProfiles: () =>
    request<BandSequenceProfile[]>("/band-sequence-profiles"),
  createBandSequenceProfile: (input: BandSequenceProfileInput) =>
    request<BandSequenceProfile>("/band-sequence-profiles", {
      method: "POST",
      body: JSON.stringify(input),
    }),
  assignBandSequenceProfile: (datasetId: string, profileId: string) =>
    request<BandSequenceProfile>(
      `/datasets/${datasetId}/band-sequence-profile`,
      { method: "PUT", body: JSON.stringify({ profile_id: profileId }) },
    ),
  deleteDataset: (datasetId: string) =>
    request<undefined>(`/datasets/${datasetId}`, { method: "DELETE" }),
  listUploads: (datasetId: string) =>
    request<UploadSession[]>(`/datasets/${datasetId}/uploads`),
  createUpload: (
    datasetId: string,
    mode: UploadMode,
    files: {
      relative_path: string;
      size_bytes: number;
      content_type?: string;
    }[],
  ) =>
    request<UploadSession>(`/datasets/${datasetId}/uploads`, {
      method: "POST",
      body: JSON.stringify({ mode, files }),
    }),
  getUploadPartUrls: (
    sessionId: string,
    objectId: string,
    partNumbers: number[],
  ) =>
    request<{ parts: { part_number: number; upload_url: string }[] }>(
      `/uploads/${sessionId}/objects/${objectId}/parts`,
      { method: "POST", body: JSON.stringify({ part_numbers: partNumbers }) },
    ),
  completeUpload: (
    sessionId: string,
    objectId: string,
    parts: { part_number: number; etag: string }[],
  ) =>
    request<UploadObject>(
      `/uploads/${sessionId}/objects/${objectId}/complete`,
      {
        method: "POST",
        body: JSON.stringify({ parts }),
      },
    ),
  markUploadFailed: (sessionId: string, objectId: string) =>
    request<UploadObject>(`/uploads/${sessionId}/objects/${objectId}/failed`, {
      method: "POST",
    }),
  retryUpload: (sessionId: string, objectId: string) =>
    request<UploadObject>(`/uploads/${sessionId}/objects/${objectId}/retry`, {
      method: "POST",
    }),
  cancelUpload: (sessionId: string) =>
    request<UploadSession>(`/uploads/${sessionId}/cancel`, { method: "POST" }),
  archiveDesktopImage: (sessionId: string, objectId: string) =>
    request<UploadObject>(`/uploads/${sessionId}/objects/${objectId}/archive`, {
      method: "POST",
    }),
  restoreDesktopImage: (sessionId: string, objectId: string) =>
    request<UploadObject>(`/uploads/${sessionId}/objects/${objectId}/restore`, {
      method: "POST",
    }),
  listCaptures: (datasetId: string) =>
    request<CaptureSummary[]>(`/datasets/${datasetId}/captures`),
  getCapture: (captureId: string) =>
    request<CaptureDetail>(`/captures/${captureId}`),
  listCalibrationPanels: () =>
    request<CalibrationPanel[]>("/calibration/panels"),
  createCalibrationPanel: (input: {
    name: string;
    manufacturer?: string;
    model?: string;
    serial_number?: string;
    calibrated_at?: string | null;
    notes?: string;
    source_label: string;
    certified: boolean;
    values: Record<string, Record<string, number>>;
    certificate_csv?: string;
  }) => request<CalibrationPanel>("/calibration/panels", {
    method: "POST", body: JSON.stringify(input),
  }),
  getCalibrationBands: (captureId: string) =>
    request<CalibrationBand[]>(`/calibration/captures/${captureId}/bands`),
  getCalibrationPixel: (captureId: string, band: string, x: number, y: number) =>
    request<{ x: number; y: number; dn: number; camera_signal: number | null }>(
      `/calibration/captures/${captureId}/pixels/${band}?x=${String(x)}&y=${String(y)}`,
    ),
  listCalibrationRuns: (datasetId: string) =>
    request<CalibrationRun[]>(`/datasets/${datasetId}/calibration-runs`),
  createCalibrationRun: (input: CalibrationRunInput) =>
    request<CalibrationRun>("/calibration/runs", {
      method: "POST", body: JSON.stringify(input),
    }),
  getCalibrationRun: (runId: string) =>
    request<CalibrationRun>(`/calibration/runs/${runId}`),
  executeCalibrationRun: (runId: string) =>
    request<CalibrationRun>(`/calibration/runs/${runId}/execute`, { method: "POST" }),
  cancelCalibrationRun: (runId: string) =>
    request<CalibrationRun>(`/calibration/runs/${runId}/cancel`, { method: "POST" }),
  retryFailedCalibrationCaptures: (runId: string) =>
    request<CalibrationRun>(`/calibration/runs/${runId}/retry-failed`, { method: "POST" }),
  getCalibrationDownload: (runId: string) =>
    request<{ url: string }>(`/calibration/runs/${runId}/download`),
  downloadCalibrationFile: (runId: string) =>
    requestDownloadBlob(`/calibration/runs/${runId}/download-file`),
  listROIs: (captureId: string) =>
    request<ROI[]>(`/captures/${captureId}/rois`),
  createROI: (captureId: string, input: ROIInput) =>
    request<ROI>(`/captures/${captureId}/rois`, {
      method: "POST",
      body: JSON.stringify(input),
    }),
  updateROI: (roiId: string, input: ROIInput) =>
    request<ROI>(`/rois/${roiId}/versions`, {
      method: "POST",
      body: JSON.stringify(input),
    }),
  deleteROI: (roiId: string) =>
    request<undefined>(`/rois/${roiId}`, { method: "DELETE" }),
  listProcessingJobs: (datasetId: string) =>
    request<ProcessingJob[]>(`/datasets/${datasetId}/processing-jobs`),
  listSpectralIndices: () =>
    request<SpectralIndexDefinition[]>("/spectral-indices"),
  getProcessingJob: (jobId: string) =>
    request<ProcessingJob>(`/processing-jobs/${jobId}`),
  createProcessingJob: (
    datasetId: string,
    input: (
      | {
          indices: {
            name: SpectralIndexName;
            parameters?: Record<string, number>;
          }[];
        }
      | { recipe_id: string }
    ) & { analysis_scopes: AnalysisScope[]; calibration_run_id?: string },
    idempotencyKey: string,
  ) =>
    request<ProcessingJob>(`/datasets/${datasetId}/processing-jobs`, {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey },
      body: JSON.stringify(input),
    }),
  cancelProcessingJob: (jobId: string) =>
    request<ProcessingJob>(`/processing-jobs/${jobId}/cancel`, {
      method: "POST",
    }),
  retryProcessingItem: (itemId: string) =>
    request<ProcessingJob>(`/processing-items/${itemId}/retry`, {
      method: "POST",
    }),
  reprocessJob: (jobId: string) =>
    request<ProcessingJob>(`/processing-jobs/${jobId}/reprocess`, {
      method: "POST",
    }),
  listProcessingRecipes: () =>
    request<ProcessingRecipe[]>("/processing-recipes"),
  createProcessingRecipe: (input: {
    name: string;
    description?: string;
    indices: {
      name: SpectralIndexName;
      parameters?: Record<string, number>;
    }[];
    statistics: string[];
  }) =>
    request<ProcessingRecipe>("/processing-recipes", {
      method: "POST",
      body: JSON.stringify(input),
    }),
  getProcessingResults: (
    jobId: string,
    filters: {
      q?: string;
      treatment?: string;
      index?: string;
      scope?: AnalysisScope;
      sort?: "treatment" | "repetition" | "index" | "mean" | "valid_pixels";
      descending?: boolean;
    } = {},
  ) => {
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(filters)) {
      if (value !== "") params.set(key, String(value));
    }
    const query = params.size ? `?${params.toString()}` : "";
    return request<ProcessingResults>(
      `/processing-jobs/${jobId}/results${query}`,
    );
  },
  getIndexResultPreview: (resultId: string) =>
    requestBlob(`/index-results/${resultId}/preview`),
  listExports: (jobId: string) =>
    request<ResultFile[]>(`/processing-jobs/${jobId}/exports`),
  createExports: (jobId: string, formats: ExportFormat[]) =>
    request<ResultFile[]>(`/processing-jobs/${jobId}/exports`, {
      method: "POST",
      body: JSON.stringify({ formats }),
    }),
};
