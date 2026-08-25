import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Job } from '../../api/types'
import { JobSummaryModal } from './JobSummaryModal'

const job: Job = {
  id: '7',
  importId: 3,
  name: 'Проверочный пакет',
  status: 'completed',
  rawState: 'completed',
  stage: 'Завершено',
  progress: 100,
  progressDone: 3,
  progressTotal: 3,
  etaSeconds: null,
  addressCount: 3,
  createdAt: '2026-07-30T12:00:00Z',
}

const summaryPayload = {
  id: 7,
  name: 'Проверочный пакет',
  status: 'completed',
  summary: {
    total: 3,
    queued: 0,
    running: 0,
    completed: 1,
    skipped: 1,
    failed: 1,
    cancelled: 0,
  },
  items: [
    {
      id: 1,
      address: '0x1111111111111111111111111111111111111111',
      state: 'completed',
      event_count: 12,
      error: null,
    },
    {
      id: 2,
      address: '0x2222222222222222222222222222222222222222',
      state: 'skipped',
      event_count: 25001,
      error: 'Более 25 000 транзакций',
    },
    {
      id: 3,
      address: '0x3333333333333333333333333333333333333333',
      state: 'failed',
      event_count: null,
      error: 'timeout',
    },
  ],
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('JobSummaryModal', () => {
  it('filters wallets by status chips', async () => {
    const user = userEvent.setup()
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(summaryPayload))))
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })

    render(
      <QueryClientProvider client={queryClient}>
        <JobSummaryModal job={job} onClose={vi.fn()} />
      </QueryClientProvider>,
    )

    expect(await screen.findByText('0x1111111111111111111111111111111111111111')).toBeInTheDocument()
    expect(screen.getByText('0x2222222222222222222222222222222222222222')).toBeInTheDocument()
    expect(screen.getByText('0x3333333333333333333333333333333333333333')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Пропущено (1)' }))
    expect(screen.getByText('0x2222222222222222222222222222222222222222')).toBeInTheDocument()
    expect(screen.queryByText('0x1111111111111111111111111111111111111111')).not.toBeInTheDocument()
    expect(screen.queryByText('0x3333333333333333333333333333333333333333')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Ошибки (1)' }))
    expect(screen.getByText('0x3333333333333333333333333333333333333333')).toBeInTheDocument()
    expect(screen.queryByText('0x2222222222222222222222222222222222222222')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Все (3)' }))
    expect(screen.getByText('0x1111111111111111111111111111111111111111')).toBeInTheDocument()
    expect(screen.getByText('0x2222222222222222222222222222222222222222')).toBeInTheDocument()
    expect(screen.getByText('0x3333333333333333333333333333333333333333')).toBeInTheDocument()
  })
})
