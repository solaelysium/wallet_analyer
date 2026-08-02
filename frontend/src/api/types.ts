export type JobStatus =
  | 'queued'
  | 'running'
  | 'completed'
  | 'completed_with_errors'
  | 'failed'
  | 'cancelled'
  | 'cancelling'

export interface PreviewEntry {
  address: string
  checksumAddress: string
  source: string
  row: number
  sourceIndex: string | null
  alreadyAnalyzed: boolean
  lastAnalyzedAt: string | null
}

export interface ImportIssue {
  kind: 'invalid' | 'duplicate' | string
  source: string
  row: number
  value: string
  detail: string
}

export interface WalletPreview {
  token: string
  validCount: number
  duplicateCount: number
  invalidCount: number
  analyzedCount: number
  sourceCount: number
  entries: PreviewEntry[]
  issues: ImportIssue[]
}

export interface Job {
  id: string
  importId: number | null
  name: string
  status: JobStatus
  stage: string
  progress: number
  etaSeconds: number | null
  addressCount: number
  createdAt: string
  error?: string
  rawState: JobStatus
  progressDone: number
  progressTotal: number
}

export interface JobSummaryWallet {
  id: number
  address: string
  state: string
  eventCount: number | null
  error: string | null
}

export interface JobSummary {
  total: number
  queued: number
  running: number
  completed: number
  skipped: number
  failed: number
  cancelled: number
  wallets: JobSummaryWallet[]
}

export interface FeatureRow {
  walletId: number
  address: string
  [key: string]: string | number | boolean | null
}

export interface FeatureColumn {
  id: string
  label: string
  type: 'string' | 'number' | 'currency' | 'percent' | 'date' | 'boolean'
  source: 'base' | 'feature' | 'quality'
}

export interface NumericFilter {
  min?: number
  max?: number
}

export interface FeatureQuery {
  page: number
  pageSize: number
  search: string
  sortBy: string
  sortDirection: 'asc' | 'desc'
  filters: Record<string, NumericFilter>
  version?: string
}

export interface FeaturePage {
  rows: FeatureRow[]
  columns: FeatureColumn[]
  page: number
  pageSize: number
  total: number
}

export interface FeatureDataset {
  version: string
  name: string
  rowCount: number
  numericFeatures: string[]
}

export interface ClusteringRequest {
  algorithm: 'hdbscan' | 'kmeans'
  reducer: 'pca' | 'umap'
  feature_version: string
  feature_names: string[]
  n_clusters: number
  min_cluster_size: number
  min_samples: number | null
  random_state: number
  umap_neighbors: number
  umap_min_dist: number
  umap_metric: 'cosine' | 'euclidean' | 'manhattan'
  reducer_components: number
  scaler: 'robust' | 'standard'
  winsorize: boolean
  winsor_lower: number
  winsor_upper: number
  log_transform: boolean
  cluster_selection_method: 'eom' | 'leaf'
}

export interface ClusterPoint {
  address: string
  x: number
  y: number
  cluster: number
  probability?: number
  values?: Record<string, number>
}

export interface ClusterProfile {
  cluster: number
  size: number
  share: number
  means: Record<string, number>
}

export interface ClusteringJob extends Job {
  request?: ClusteringRequest
  points?: ClusterPoint[]
  profiles?: ClusterProfile[]
}

export type ProviderService = 'etherscan' | 'infura' | 'coingecko'

export interface ProviderHealth {
  service: ProviderService
  enabledKeys: number
  totalKeys: number
  healthyKeys: number
  status: 'ready' | 'degraded' | 'unavailable'
  message?: string
}

export interface RuntimeSettings {
  job_workers: number
  provider_timeout_seconds: number
  provider_max_retries: number
  provider_cooldown_seconds: number
  etherscan_rps: number
  infura_rps: number
  coingecko_rps: number
  key_concurrency: number
  provider_health: ProviderHealth[]
  message?: string
}

export type RuntimeSettingsUpdate = Omit<RuntimeSettings, 'provider_health' | 'message'>

export interface ApiKeyRecord {
  id: number
  service: ProviderService
  label: string
  maskedValue: string
  enabled: boolean
  errorCount: number
  lastUsedAt: string | null
  createdAt: string | null
}

export interface ApiKeyCreate {
  service: ProviderService
  label: string
  value: string
}

export interface ApiKeyUpdate {
  label?: string
  value?: string
  enabled?: boolean
}

export interface LogEntry {
  id: number
  level: string
  event: string
  message: string
  context: Record<string, unknown>
  jobId: number | null
  jobItemId: number | null
  clusterRunId: number | null
  createdAt: string
}

export interface LogQuery {
  page: number
  pageSize: number
  level: string
  event: string
  search: string
  jobId: string
  clusterRunId: string
}

export interface LogPage {
  items: LogEntry[]
  total: number
  page: number
  pageSize: number
}

export interface ApiErrorBody {
  detail?: string
  message?: string
}
