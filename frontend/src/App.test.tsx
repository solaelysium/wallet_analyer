import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'

vi.mock('react-plotly.js/factory', () => ({ default: () => () => <div data-testid="plot" /> }))
vi.mock('plotly.js-gl2d-dist-min', () => ({ default: { downloadImage: vi.fn() } }))

describe('application navigation', () => {
  beforeEach(() => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: string | URL | Request) => {
        const url = String(input)
        if (url.includes('/features')) {
          return new Response(JSON.stringify({ items: [], page: 1, size: 100, total: 0 }))
        }
        if (url.endsWith('/settings')) {
          return new Response(JSON.stringify({
            job_workers: 4,
            provider_timeout_seconds: 30,
            provider_max_retries: 4,
            provider_cooldown_seconds: 30,
            etherscan_rps: 4,
            infura_rps: 8,
            coingecko_rps: 1,
            key_concurrency: 2,
            provider_health: {},
          }))
        }
        return new Response(JSON.stringify({ items: [], total: 0 }))
      }),
    )
  })

  it('shows all five work pages and changes the active page', async () => {
    const user = userEvent.setup()
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={queryClient}>
        <App />
      </QueryClientProvider>,
    )

    expect(screen.getByRole('navigation').querySelectorAll('button')).toHaveLength(5)
    expect(await screen.findByRole('heading', { name: 'Кошельки' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Признаки' }))
    expect(await screen.findByRole('heading', { name: 'Признаки' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Кластеризация' }))
    expect(await screen.findByRole('heading', { name: 'Кластеризация' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Журнал' }))
    expect(await screen.findByRole('heading', { name: 'Журнал событий' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Настройки' }))
    expect(await screen.findByRole('heading', { name: 'Настройки' })).toBeInTheDocument()
  })
})
