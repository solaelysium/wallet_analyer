import type {
  ApiKeyCreate,
  ApiKeyRecord,
  ApiKeyUpdate,
  ApiErrorBody,
  ClusterProfile,
  ClusteringJob,
  ClusteringRequest,
  FeatureColumn,
  FeatureDataset,
  FeaturePage,
  FeatureQuery,
  FeatureRow,
  Job,
  JobStatus,
  LogPage,
  LogQuery,
  ProviderHealth,
  ProviderService,
  RuntimeSettings,
  RuntimeSettingsUpdate,
  WalletPreview,
} from './types'

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? '/api'

interface RawPreview {
  token: string
  valid_count: number
  duplicate_count: number
  invalid_count: number
  source_count: number
  entries: {
    address: string
    checksum_address: string
    source: string
    row: number
    source_index: string | null
  }[]
  issues: {
    kind: string
    source: string
    row: number
    value: string
    detail: string
  }[]
}

interface RawJob {
  id: number
  kind: string
  state: string
  wallet_import_id: number | null
  cancel_requested: boolean
  progress_done: number
  progress_total: number
  error: string | null
  parameters: Record<string, unknown>
  created_at: string
  started_at: string | null
  finished_at: string | null
  items?: { state: string; stage: string | null; error: string | null }[]
}

interface RawImport {
  id: number
  name: string
  wallet_count: number
  created_at: string
}

interface RawFeatureRow {
  snapshot_id: number
  wallet_id: number
  address: string
  version: string
  as_of_block: number
  created_at: string
  features: Record<string, unknown>
  quality: Record<string, unknown>
}

interface RawFeaturePage {
  items: RawFeatureRow[]
  total: number
  page: number
  size: number
}

interface RawClusterRun {
  id: number
  state: string
  algorithm: 'hdbscan' | 'kmeans'
  reducer: 'pca' | 'umap'
  feature_version: string
  parameters: Record<string, unknown>
  feature_names: string[]
  metrics: Record<string, unknown>
  profiles: Record<string, { size?: number; means?: Record<string, number> }>
  stage: string
  progress_percent: number
  error: string | null
  cancel_requested: boolean
  created_at: string
  started_at: string | null
  finished_at: string | null
  assignments?: {
    wallet_id: number
    address: string
    cluster: number
    probability: number | null
    x: number
    y: number
    features?: Record<string, number>
  }[]
}

interface PageResponse<T> {
  items: T[]
  total: number
}

interface RawApiKey {
  id: number
  service: ProviderService
  label: string
  masked_value?: string
  value_masked?: string
  masked?: string
  enabled: boolean
  error_count?: number
  last_used_at?: string | null
  created_at?: string | null
}

interface RawLogEntry {
  id: number
  level: string
  event: string
  message: string
  context: Record<string, unknown>
  job_id: number | null
  job_item_id: number | null
  cluster_run_id: number | null
  created_at: string
}

interface RawLogPage {
  items: RawLogEntry[]
  total: number
  page: number
  size: number
}

export class ApiError extends Error {
  readonly status: number

  constructor(message: string, status: number) {
    super(message)
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, init)
  if (!response.ok) {
    let body: ApiErrorBody = {}
    try {
      body = (await response.json()) as ApiErrorBody
    } catch {
      // Error responses are not guaranteed to contain JSON.
    }
    throw new ApiError(body.detail ?? body.message ?? `Ошибка запроса (${response.status})`, response.status)
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

function jsonRequest(method: string, body?: unknown): RequestInit {
  return {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  }
}

function queryString(values: Record<string, string | number | undefined>) {
  const params = new URLSearchParams()
  Object.entries(values).forEach(([key, value]) => {
    if (value !== undefined && value !== '') params.set(key, String(value))
  })
  return params.toString()
}

const jobStates = new Set<JobStatus>([
  'queued',
  'running',
  'completed',
  'completed_with_errors',
  'failed',
  'cancelled',
  'cancelling',
])

function normalizeState(value: string): JobStatus {
  return jobStates.has(value as JobStatus) ? (value as JobStatus) : 'failed'
}

function titleCase(value: string) {
  return value.replaceAll('_', ' ').replace(/\b\w/g, (character) => character.toUpperCase())
}

const stageLabels: Record<string, string> = {
  queued: 'В очереди',
  running: 'Выполняется',
  completed: 'Завершено',
  completed_with_errors: 'Завершено с ошибками',
  failed: 'Ошибка',
  cancelled: 'Отменено',
  cancelling: 'Отменяется',
  pending: 'Ожидание',
  recovered: 'Восстановлено',
  preparing: 'Подготовка',
  collecting: 'Сбор данных',
  preprocessing: 'Подготовка данных',
  reducing: 'Снижение размерности',
  reducing_and_clustering: 'Снижение размерности и кластеризация',
  clustering: 'Кластеризация',
  persisting: 'Сохранение результатов',
  stopping: 'Остановка',
  rpc: 'Запросы RPC',
}

function stageLabel(value: string) {
  return stageLabels[value.toLowerCase()] ?? titleCase(value)
}

function jobError(value: string | null | undefined) {
  if (value === 'Interrupted by backend restart') return 'Прервано из-за перезапуска сервера'
  return value ?? undefined
}

function normalizeApiKey(raw: RawApiKey): ApiKeyRecord {
  return {
    id: raw.id,
    service: raw.service,
    label: raw.label,
    maskedValue: raw.masked_value ?? raw.value_masked ?? raw.masked ?? '••••••••',
    enabled: raw.enabled,
    errorCount: raw.error_count ?? 0,
    lastUsedAt: raw.last_used_at ?? null,
    createdAt: raw.created_at ?? null,
  }
}

function normalizeProviderHealth(
  raw: unknown,
  keys: ApiKeyRecord[] = [],
): ProviderHealth[] {
  const services: ProviderService[] = ['etherscan', 'infura', 'coingecko']
  const health = raw && typeof raw === 'object' ? raw as Record<string, unknown> : {}
  return services.map((service) => {
    const serviceKeys = keys.filter((key) => key.service === service)
    const value = health[service]
    const entries = Array.isArray(value)
      ? value.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === 'object')
      : []
    const detail = value && typeof value === 'object' && !Array.isArray(value)
      ? value as Record<string, unknown>
      : {}
    const totalKeys = Number(detail.total_keys ?? detail.total ?? (entries.length || serviceKeys.length))
    const enabledKeys = Number(
      detail.enabled_keys
      ?? detail.enabled
      ?? (entries.length
        ? entries.filter((item) => item.enabled !== false).length
        : undefined)
      ?? serviceKeys.filter((key) => key.enabled).length,
    )
    const healthyKeys = Number(
      detail.healthy_keys
      ?? detail.healthy
      ?? (entries.length
        ? entries.filter((item) =>
          item.enabled !== false
          && item.active !== false
          && Number(item.cooldown_seconds ?? 0) <= 0
          && !item.last_error,
        ).length
        : enabledKeys),
    )
    const status =
      detail.status === 'ready' || detail.status === 'degraded' || detail.status === 'unavailable'
        ? detail.status
        : enabledKeys === 0
          ? 'unavailable'
          : healthyKeys < enabledKeys
            ? 'degraded'
            : 'ready'
    return {
      service,
      totalKeys,
      enabledKeys,
      healthyKeys,
      status,
      message: typeof detail.message === 'string' ? detail.message : undefined,
    }
  })
}

export function normalizePreview(raw: RawPreview): WalletPreview {
  return {
    token: raw.token,
    validCount: raw.valid_count,
    duplicateCount: raw.duplicate_count,
    invalidCount: raw.invalid_count,
    sourceCount: raw.source_count,
    entries: raw.entries.map((entry) => ({
      address: entry.address,
      checksumAddress: entry.checksum_address,
      source: entry.source === 'manual' ? 'Ручной ввод' : entry.source,
      row: entry.row,
      sourceIndex: entry.source_index,
    })),
    issues: raw.issues.map((issue) => ({
      ...issue,
      source: issue.source === 'manual' ? 'Ручной ввод' : issue.source,
    })),
  }
}

export function normalizeJob(raw: RawJob, importName?: string): Job {
  const status = normalizeState(raw.state)
  const total = raw.progress_total ?? 0
  const done = raw.progress_done ?? 0
  const activeItem = raw.items?.find((item) => item.state === 'running' && item.stage)
    ?? raw.items?.find((item) => item.stage)
  const terminal = ['completed', 'completed_with_errors', 'failed', 'cancelled'].includes(status)
  return {
    id: String(raw.id),
    importId: raw.wallet_import_id,
    name: importName ?? `${raw.kind === 'collection' ? 'Сбор данных' : titleCase(raw.kind)} №${raw.id}`,
    status,
    rawState: status,
    stage: activeItem?.stage ? stageLabel(activeItem.stage) : stageLabel(raw.state),
    progress: total > 0 ? (done / total) * 100 : terminal ? 100 : 0,
    progressDone: done,
    progressTotal: total,
    etaSeconds: null,
    addressCount: total,
    createdAt: raw.created_at,
    error: jobError(raw.error ?? raw.items?.find((item) => item.error)?.error),
  }
}

function featureValueType(id: string, values: unknown[]): FeatureColumn['type'] {
  const present = values.find((value) => value !== null && value !== undefined)
  if (id.endsWith('_at')) return 'date'
  if (typeof present === 'boolean') return 'boolean'
  if (typeof present === 'number') {
    if (/(^|_)usd($|_)/i.test(id)) return 'currency'
    if (/(ratio|rate|share|percent)/i.test(id)) return 'percent'
    return 'number'
  }
  return 'string'
}

function labelForColumn(id: string) {
  return titleCase(id.replace(/^quality\./, 'Качество: '))
}

export function normalizeFeaturePage(raw: RawFeaturePage): FeaturePage {
  const rows: FeatureRow[] = raw.items.map((item) => {
    const row: FeatureRow = {
      walletId: item.wallet_id,
      address: item.address,
      version: item.version,
      as_of_block: item.as_of_block,
      created_at: item.created_at,
    }
    Object.entries(item.features).forEach(([key, value]) => {
      row[key] = value as FeatureRow[string]
    })
    Object.entries(item.quality).forEach(([key, value]) => {
      row[`quality.${key}`] = value as FeatureRow[string]
    })
    return row
  })
  const baseIds = ['address', 'version', 'as_of_block', 'created_at']
  const featureIds = Array.from(new Set(raw.items.flatMap((item) => Object.keys(item.features)))).sort()
  const qualityIds = Array.from(new Set(raw.items.flatMap((item) => Object.keys(item.quality))))
    .sort()
    .map((id) => `quality.${id}`)
  const columns: FeatureColumn[] = [...baseIds, ...featureIds, ...qualityIds].map((id) => ({
    id,
    label: labelForColumn(id),
    type: featureValueType(id, rows.map((row) => row[id])),
    source: baseIds.includes(id) ? 'base' : id.startsWith('quality.') ? 'quality' : 'feature',
  }))
  return {
    rows,
    columns,
    page: Math.max(0, raw.page - 1),
    pageSize: raw.size,
    total: raw.total,
  }
}

export function normalizeClusterRun(raw: RawClusterRun): ClusteringJob {
  const status = normalizeState(raw.state)
  const assignments = raw.assignments ?? []
  const profileEntries = Object.entries(raw.profiles ?? {})
  const profileTotal =
    typeof raw.metrics.samples === 'number'
      ? raw.metrics.samples
      : profileEntries.reduce((sum, [, profile]) => sum + (profile.size ?? 0), 0)
  const profiles: ClusterProfile[] = profileEntries.map(([cluster, profile]) => ({
    cluster: Number(cluster),
    size: profile.size ?? 0,
    share: profileTotal > 0 ? (profile.size ?? 0) / profileTotal : 0,
    means: profile.means ?? {},
  }))
  const parameters = raw.parameters
  const requestBody: ClusteringRequest = {
    algorithm: raw.algorithm,
    reducer: raw.reducer,
    feature_version: raw.feature_version,
    feature_names: raw.feature_names ?? [],
    n_clusters: Number(parameters.n_clusters ?? 3),
    min_cluster_size: Number(parameters.min_cluster_size ?? 5),
    min_samples: parameters.min_samples == null ? null : Number(parameters.min_samples),
    random_state: Number(parameters.random_state ?? 42),
    umap_neighbors: Number(parameters.umap_neighbors ?? 15),
    umap_min_dist: Number(parameters.umap_min_dist ?? 0.1),
    umap_metric: (parameters.umap_metric as ClusteringRequest['umap_metric']) ?? 'cosine',
    reducer_components: Number(parameters.reducer_components ?? 5),
    scaler: (parameters.scaler as ClusteringRequest['scaler']) ?? 'robust',
    winsorize: Boolean(parameters.winsorize ?? true),
    winsor_lower: Number(parameters.winsor_lower ?? 0.01),
    winsor_upper: Number(parameters.winsor_upper ?? 0.99),
    log_transform: Boolean(parameters.log_transform ?? false),
    cluster_selection_method:
      (parameters.cluster_selection_method as ClusteringRequest['cluster_selection_method']) ?? 'eom',
  }
  return {
    id: String(raw.id),
    importId: null,
    name: `${raw.algorithm.toUpperCase()} · ${raw.reducer.toUpperCase()}`,
    status,
    rawState: status,
    stage: stageLabel(raw.stage || raw.state),
    progress: raw.progress_percent ?? (status === 'completed' ? 100 : 0),
    progressDone: assignments.length,
    progressTotal: profileTotal,
    etaSeconds: null,
    addressCount: profileTotal,
    createdAt: raw.created_at,
    error: jobError(raw.error),
    request: requestBody,
    points: raw.assignments?.map((assignment) => ({
      address: assignment.address,
      x: assignment.x,
      y: assignment.y,
      cluster: assignment.cluster,
      probability: assignment.probability ?? undefined,
      values: assignment.features,
    })),
    profiles,
  }
}

export async function previewWalletSources(files: File[], manual: string): Promise<WalletPreview> {
  const body = new FormData()
  files.forEach((file) => body.append('files', file))
  if (manual.trim()) body.append('manual_text', manual)
  return normalizePreview(await request<RawPreview>('/imports/preview', { method: 'POST', body }))
}

export async function createWalletBatch(
  token: string,
  name: string,
  chain: string,
): Promise<Job> {
  const response = await request<{ import: RawImport; job: RawJob }>(
    '/imports/confirm',
    jsonRequest('POST', { token, name, chain }),
  )
  return normalizeJob(response.job, response.import.name)
}

export async function getWalletJobs(): Promise<Job[]> {
  const [jobsPage, importsPage] = await Promise.all([
    request<PageResponse<RawJob>>('/jobs?page=1&size=50'),
    request<PageResponse<RawImport>>('/imports?page=1&size=50'),
  ])
  const imports = new Map(importsPage.items.map((item) => [item.id, item]))
  const detailed = await Promise.all(
    jobsPage.items.map(async (job) => {
      if (!['queued', 'running', 'cancelling'].includes(job.state)) return job
      try {
        return await request<RawJob>(`/jobs/${job.id}`)
      } catch {
        return job
      }
    }),
  )
  return detailed.map((job) =>
    normalizeJob(job, job.wallet_import_id ? imports.get(job.wallet_import_id)?.name : undefined),
  )
}

export async function controlWalletJob(
  id: string,
  action: 'stop' | 'resume' | 'retry' | 'recalculate',
): Promise<Job> {
  const raw = await request<RawJob>(
    `/jobs/${encodeURIComponent(id)}/${action}`,
    jsonRequest('POST'),
  )
  return normalizeJob(raw)
}

export async function deleteWalletBatch(importId: number): Promise<void> {
  await request<void>(`/imports/${importId}`, { method: 'DELETE' })
}

function featureParams(input: FeatureQuery, includeFilters = true) {
  return queryString({
    page: input.page + 1,
    size: input.pageSize,
    version: input.version,
    search: includeFilters ? input.search : undefined,
    sort_by: input.sortBy,
    sort_order: input.sortDirection,
    filters:
      includeFilters && Object.keys(input.filters).length
        ? JSON.stringify(input.filters)
        : undefined,
  })
}

export async function getFeatures(input: FeatureQuery): Promise<FeaturePage> {
  return normalizeFeaturePage(await request<RawFeaturePage>(`/features?${featureParams(input)}`))
}

export async function deleteWallet(walletId: number): Promise<void> {
  await request<void>(`/wallets/${walletId}`, { method: 'DELETE' })
}

export async function getCurrentFeatureDataset(): Promise<FeatureDataset | null> {
  const latest = await request<RawFeaturePage>(
    '/features?page=1&size=1&sort_by=created_at&sort_order=desc',
  )
  if (!latest.items.length) return null
  const version = latest.items[0].version
  const raw = await request<RawFeaturePage>(
    `/features?${queryString({
      version,
      page: 1,
      size: 500,
      sort_by: 'created_at',
      sort_order: 'desc',
    })}`,
  )
  const numericFeatures = Object.keys(raw.items[0]?.features ?? {})
    .filter((name) =>
      raw.items.every((item) => {
        const value = item.features[name]
        return typeof value === 'number' && !Number.isNaN(value)
      }),
    )
    .sort()
  return {
    version,
    name: `Текущие признаки · ${version}`,
    rowCount: raw.total,
    numericFeatures,
  }
}

export async function exportFeatures(
  input: FeatureQuery,
  format: 'csv' | 'xlsx',
  scope: 'filtered' | 'all',
): Promise<Blob> {
  const query = queryString({
    file_format: format,
    version: input.version,
    search: scope === 'filtered' ? input.search : undefined,
    filters:
      scope === 'filtered' && Object.keys(input.filters).length
        ? JSON.stringify(input.filters)
        : undefined,
    sort_by: input.sortBy,
    sort_order: input.sortDirection,
  })
  const response = await fetch(`${API_BASE}/features/export?${query}`)
  if (!response.ok) throw new ApiError(`Ошибка экспорта (${response.status})`, response.status)
  return response.blob()
}

export async function getClusteringJobDetail(id: string): Promise<ClusteringJob> {
  return normalizeClusterRun(await request<RawClusterRun>(`/clusters/${encodeURIComponent(id)}`))
}

export async function getClusteringJobs(): Promise<ClusteringJob[]> {
  const page = await request<PageResponse<RawClusterRun>>('/clusters?page=1&size=50')
  const results = await Promise.allSettled(
    page.items.map((run) =>
      run.state === 'completed'
        ? request<RawClusterRun>(`/clusters/${run.id}`)
        : Promise.resolve(run),
    ),
  )
  return results.map((result, index) =>
    normalizeClusterRun(result.status === 'fulfilled' ? result.value : page.items[index]),
  )
}

export async function startClustering(body: ClusteringRequest): Promise<ClusteringJob> {
  return normalizeClusterRun(
    await request<RawClusterRun>('/clusters', jsonRequest('POST', body)),
  )
}

export async function stopClustering(id: string): Promise<ClusteringJob> {
  return normalizeClusterRun(
    await request<RawClusterRun>(
      `/clusters/${encodeURIComponent(id)}/stop`,
      jsonRequest('POST'),
    ),
  )
}

export async function exportAssignments(id: string, format: 'csv' | 'xlsx'): Promise<Blob> {
  const response = await fetch(
    `${API_BASE}/clusters/${encodeURIComponent(id)}/export?file_format=${format}`,
  )
  if (!response.ok) throw new ApiError(`Ошибка экспорта (${response.status})`, response.status)
  return response.blob()
}

export async function getRuntimeSettings(): Promise<RuntimeSettings> {
  const raw = await request<Record<string, unknown>>('/settings')
  const values = (raw.settings && typeof raw.settings === 'object'
    ? raw.settings
    : raw) as Record<string, unknown>
  return {
    job_workers: Number(values.job_workers),
    provider_timeout_seconds: Number(values.provider_timeout_seconds),
    provider_max_retries: Number(values.provider_max_retries),
    provider_cooldown_seconds: Number(values.provider_cooldown_seconds),
    etherscan_rps: Number(values.etherscan_rps),
    infura_rps: Number(values.infura_rps),
    coingecko_rps: Number(values.coingecko_rps),
    key_concurrency: Number(values.key_concurrency),
    provider_health: normalizeProviderHealth(raw.provider_health ?? values.provider_health),
    message: typeof raw.message === 'string' ? raw.message : undefined,
  }
}

export async function updateRuntimeSettings(
  values: RuntimeSettingsUpdate,
): Promise<RuntimeSettings> {
  const raw = await request<Record<string, unknown>>('/settings', jsonRequest('PATCH', values))
  const settings = (raw.settings && typeof raw.settings === 'object'
    ? raw.settings
    : raw) as Record<string, unknown>
  return {
    job_workers: Number(settings.job_workers),
    provider_timeout_seconds: Number(settings.provider_timeout_seconds),
    provider_max_retries: Number(settings.provider_max_retries),
    provider_cooldown_seconds: Number(settings.provider_cooldown_seconds),
    etherscan_rps: Number(settings.etherscan_rps),
    infura_rps: Number(settings.infura_rps),
    coingecko_rps: Number(settings.coingecko_rps),
    key_concurrency: Number(settings.key_concurrency),
    provider_health: normalizeProviderHealth(raw.provider_health ?? settings.provider_health),
    message: typeof raw.message === 'string' ? raw.message : 'Настройки применены.',
  }
}

export async function getApiKeys(): Promise<ApiKeyRecord[]> {
  const raw = await request<RawApiKey[] | PageResponse<RawApiKey>>('/api-keys')
  const items = Array.isArray(raw) ? raw : raw.items
  return items.map(normalizeApiKey)
}

export async function createApiKey(values: ApiKeyCreate): Promise<ApiKeyRecord> {
  return normalizeApiKey(
    await request<RawApiKey>('/api-keys', jsonRequest('POST', values)),
  )
}

export async function updateApiKey(
  id: number,
  values: ApiKeyUpdate,
): Promise<ApiKeyRecord> {
  return normalizeApiKey(
    await request<RawApiKey>(`/api-keys/${id}`, jsonRequest('PATCH', values)),
  )
}

export async function deleteApiKey(id: number): Promise<void> {
  await request<void>(`/api-keys/${id}`, { method: 'DELETE' })
}

export async function getLogs(input: LogQuery): Promise<LogPage> {
  const raw = await request<RawLogPage>(`/logs?${queryString({
    page: input.page + 1,
    size: input.pageSize,
    level: input.level,
    event: input.event,
    search: input.search,
    job_id: input.jobId,
    cluster_run_id: input.clusterRunId,
  })}`)
  return {
    items: raw.items.map((item) => ({
      id: item.id,
      level: item.level,
      event: item.event,
      message: item.message,
      context: item.context ?? {},
      jobId: item.job_id,
      jobItemId: item.job_item_id,
      clusterRunId: item.cluster_run_id,
      createdAt: item.created_at,
    })),
    total: raw.total,
    page: Math.max(0, raw.page - 1),
    pageSize: raw.size,
  }
}
