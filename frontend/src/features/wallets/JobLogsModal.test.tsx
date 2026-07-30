import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Job } from '../../api/types'
import { JobLogsModal } from './JobLogsModal'


const job: Job = {
  id: '7',
  importId: 3,
  name: 'Проверочный пакет',
  status: 'completed',
  rawState: 'completed',
  stage: 'Завершено',
  progress: 100,
  progressDone: 1,
  progressTotal: 1,
  etaSeconds: null,
  addressCount: 1,
  createdAt: '2026-07-30T12:00:00Z',
}


afterEach(() => {
  vi.unstubAllGlobals()
})


describe('JobLogsModal', () => {
  it('loads logs scoped to the selected job', async () => {
    const fetchMock = vi.fn(async (_input: string | URL | Request) => new Response(JSON.stringify({
      items: [{
        id: 1,
        level: 'info',
        event: 'job.finished',
        message: 'Job finished',
        context: {},
        job_id: 7,
        job_item_id: null,
        cluster_run_id: null,
        created_at: '2026-07-30T12:00:00Z',
      }],
      total: 1,
      page: 1,
      size: 200,
    })))
    vi.stubGlobal('fetch', fetchMock)
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })

    render(
      <QueryClientProvider client={queryClient}>
        <JobLogsModal job={job} onClose={vi.fn()} />
      </QueryClientProvider>,
    )

    expect(await screen.findByText(/job.finished: Job finished/)).toBeInTheDocument()
    expect(String(fetchMock.mock.calls[0][0])).toContain('job_id=7')
  })
})
