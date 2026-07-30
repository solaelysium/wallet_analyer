import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  createWalletBatch,
  getClusteringJobs,
  getFeatures,
  normalizeJob,
  previewWalletSources,
} from './client'

const backendJob = {
  id: 17,
  kind: 'collection',
  state: 'completed_with_errors',
  wallet_import_id: 4,
  cancel_requested: false,
  progress_done: 8,
  progress_total: 10,
  error: 'Two wallets failed',
  parameters: { chain: 'ethereum' },
  created_at: '2026-07-29T20:00:00+00:00',
  started_at: '2026-07-29T20:00:01+00:00',
  finished_at: '2026-07-29T20:01:00+00:00',
}

afterEach(() => vi.unstubAllGlobals())

describe('backend API contract adapters', () => {
  it('posts multipart import preview using manual_text and maps entries and issues', async () => {
    const fetchMock = vi.fn(async (_input: string | URL | Request, init?: RequestInit) => {
      const body = init?.body as FormData
      expect(body.get('manual_text')).toBe('0xabc')
      expect(body.getAll('files')).toHaveLength(1)
      return new Response(JSON.stringify({
        token: 'sealed-token',
        valid_count: 1,
        duplicate_count: 1,
        invalid_count: 1,
        source_count: 2,
        entries: [{
          address: '0x1111111111111111111111111111111111111111',
          checksum_address: '0x1111111111111111111111111111111111111111',
          source: 'wallets.csv',
          row: 2,
          source_index: 'a-1',
        }],
        issues: [{
          kind: 'duplicate',
          source: 'manual',
          row: 1,
          value: '0x1111111111111111111111111111111111111111',
          detail: 'First seen at wallets.csv:2',
        }],
      }))
    })
    vi.stubGlobal('fetch', fetchMock)

    const preview = await previewWalletSources([new File(['wallet_address'], 'wallets.csv')], '0xabc')

    expect(fetchMock.mock.calls[0][0]).toBe('/api/imports/preview')
    expect(preview).toMatchObject({
      token: 'sealed-token',
      validCount: 1,
      duplicateCount: 1,
      invalidCount: 1,
      sourceCount: 2,
    })
    expect(preview.entries[0].sourceIndex).toBe('a-1')
    expect(preview.issues[0].detail).toContain('First seen')
  })

  it('confirms an import with the exact payload and preserves job errors and progress', async () => {
    const fetchMock = vi.fn(async (_input: string | URL | Request, init?: RequestInit) => {
      expect(JSON.parse(String(init?.body))).toEqual({
        token: 'sealed-token',
        name: 'July wallets',
        chain: 'ethereum',
      })
      return new Response(JSON.stringify({
        import: {
          id: 4,
          name: 'July wallets',
          wallet_count: 10,
          created_at: '2026-07-29T20:00:00+00:00',
        },
        job: backendJob,
      }), { status: 201 })
    })
    vi.stubGlobal('fetch', fetchMock)

    const job = await createWalletBatch('sealed-token', 'July wallets', 'ethereum')

    expect(fetchMock.mock.calls[0][0]).toBe('/api/imports/confirm')
    expect(job).toMatchObject({
      name: 'July wallets',
      status: 'completed_with_errors',
      progress: 80,
      progressDone: 8,
      progressTotal: 10,
      error: 'Two wallets failed',
    })
  })

  it('uses one-based feature pagination and flattens nested features and quality', async () => {
    const fetchMock = vi.fn(async (_input: string | URL | Request) => new Response(JSON.stringify({
      items: [{
        snapshot_id: 9,
        wallet_id: 3,
        address: '0x1111111111111111111111111111111111111111',
        version: 'wallet_features.v1',
        as_of_block: 123,
        created_at: '2026-07-29T20:00:00+00:00',
        features: { balance_usd: 120.5, tx_count: 8 },
        quality: { price_coverage: 0.9 },
      }],
      total: 51,
      page: 1,
      size: 50,
    })))
    vi.stubGlobal('fetch', fetchMock)

    const page = await getFeatures({
      page: 0,
      pageSize: 50,
      search: '0x11',
      sortBy: 'tx_count',
      sortDirection: 'desc',
      filters: { tx_count: { min: 3, max: 20 } },
    })

    const requestUrl = String(fetchMock.mock.calls[0][0])
    expect(requestUrl).toContain('/api/features?')
    expect(requestUrl).toContain('page=1')
    expect(requestUrl).toContain('size=50')
    expect(requestUrl).toContain('sort_order=desc')
    expect(page.rows[0]).toMatchObject({
      address: '0x1111111111111111111111111111111111111111',
      balance_usd: 120.5,
      tx_count: 8,
      'quality.price_coverage': 0.9,
    })
    expect(page.columns.find((column) => column.id === 'tx_count')?.source).toBe('feature')
  })

  it('fetches completed cluster details and transforms assignments and profile objects', async () => {
    const run = {
      id: 12,
      state: 'completed',
      algorithm: 'kmeans',
      reducer: 'pca',
      feature_version: 'wallet_features.v1',
      parameters: {
        algorithm: 'kmeans',
        reducer: 'pca',
        n_clusters: 3,
        min_cluster_size: 5,
        min_samples: null,
        random_state: 42,
        umap_neighbors: 15,
      },
      feature_names: ['tx_count', 'balance_usd'],
      metrics: { samples: 4, cluster_count: 2, noise_count: 0 },
      profiles: {
        '0': { size: 3, means: { tx_count: 7, balance_usd: 100 } },
        '1': { size: 1, means: { tx_count: 2, balance_usd: 10 } },
      },
      error: null,
      cancel_requested: false,
      created_at: '2026-07-29T20:00:00+00:00',
      started_at: '2026-07-29T20:00:01+00:00',
      finished_at: '2026-07-29T20:01:00+00:00',
    }
    const fetchMock = vi.fn(async (input: string | URL | Request) => {
      const url = String(input)
      if (url === '/api/clusters?page=1&size=50') {
        return new Response(JSON.stringify({ items: [run], total: 1 }))
      }
      return new Response(JSON.stringify({
        ...run,
        assignments: [{
          wallet_id: 3,
          address: '0x1111111111111111111111111111111111111111',
          cluster: 0,
          probability: null,
          x: 1.2,
          y: -0.4,
        }],
      }))
    })
    vi.stubGlobal('fetch', fetchMock)

    const jobs = await getClusteringJobs()

    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(fetchMock.mock.calls[1][0]).toBe('/api/clusters/12')
    expect(jobs[0].points?.[0]).toMatchObject({ cluster: 0, x: 1.2, y: -0.4 })
    expect(jobs[0].profiles?.[0]).toMatchObject({ cluster: 0, size: 3, share: 0.75 })
  })

  it('maps every backend collection state without dropping the raw state', () => {
    const states = [
      'queued',
      'running',
      'completed',
      'completed_with_errors',
      'failed',
      'cancelled',
      'cancelling',
    ] as const
    expect(states.map((state) => normalizeJob({ ...backendJob, state }).rawState)).toEqual(states)
  })
})
