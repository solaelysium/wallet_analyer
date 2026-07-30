import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { SettingsPage } from './SettingsPage'

const runtimeSettings = {
  job_workers: 4,
  provider_timeout_seconds: 30,
  provider_max_retries: 4,
  provider_cooldown_seconds: 30,
  etherscan_rps: 4,
  infura_rps: 8,
  coingecko_rps: 1,
  key_concurrency: 2,
  provider_health: {
    etherscan: { total_keys: 1, enabled_keys: 1, healthy_keys: 1, status: 'ready' },
    infura: { total_keys: 0, enabled_keys: 0, healthy_keys: 0, status: 'unavailable' },
    coingecko: { total_keys: 0, enabled_keys: 0, healthy_keys: 0, status: 'unavailable' },
  },
}

const apiKey = {
  id: 7,
  service: 'etherscan',
  label: 'Primary',
  masked_value: 'abcd••••wxyz',
  enabled: true,
  error_count: 0,
  last_used_at: null,
  created_at: '2026-07-29T20:00:00Z',
}

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <SettingsPage />
    </QueryClientProvider>,
  )
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('SettingsPage', () => {
  it('shows provider health and masked keys without exposing plaintext secrets', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: string | URL | Request) => {
      const url = String(input)
      return new Response(JSON.stringify(url.endsWith('/settings') ? runtimeSettings : [apiKey]))
    }))

    renderPage()

    expect(await screen.findByText('abcd••••wxyz')).toBeInTheDocument()
    expect(screen.queryByText('actual-secret-value')).not.toBeInTheDocument()
    expect(screen.getByText('Работают ключи: 1/1')).toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: 'Отключить Primary' })).toBeChecked()
  })

  it('applies runtime values using the settings PATCH contract', async () => {
    const fetchMock = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/settings') && init?.method === 'PATCH') {
        return new Response(JSON.stringify({
          ...runtimeSettings,
          ...JSON.parse(String(init.body)),
          message: 'Настройки применены.',
        }))
      }
      return new Response(JSON.stringify(url.endsWith('/settings') ? runtimeSettings : [apiKey]))
    })
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    renderPage()

    const workers = await screen.findByRole('spinbutton', { name: 'Обработчики задач' })
    await user.clear(workers)
    await user.type(workers, '6')
    await user.click(screen.getByRole('button', { name: 'Применить настройки' }))

    expect(await screen.findByText('Настройки применены.')).toBeInTheDocument()
    const request = fetchMock.mock.calls.find(([, init]) => init?.method === 'PATCH')
    expect(request?.[0]).toBe('/api/settings')
    expect(JSON.parse(String(request?.[1]?.body))).toMatchObject({
      job_workers: 6,
      key_concurrency: 2,
      etherscan_rps: 4,
    })
  })

  it('supports adding, disabling, editing, rotating, and deleting a key', async () => {
    const fetchMock = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      if (url.endsWith('/settings')) return new Response(JSON.stringify(runtimeSettings))
      if (url === '/api/api-keys' && method === 'GET') return new Response(JSON.stringify([apiKey]))
      if (url === '/api/api-keys' && method === 'POST') {
        return new Response(JSON.stringify({
          id: 8,
          ...JSON.parse(String(init?.body)),
          masked_value: 'new••••key',
          enabled: true,
        }), { status: 201 })
      }
      if (url === '/api/api-keys/7' && method === 'PATCH') {
        return new Response(JSON.stringify({
          ...apiKey,
          ...JSON.parse(String(init?.body)),
          masked_value: 'rotated••••key',
        }))
      }
      if (url === '/api/api-keys/7' && method === 'DELETE') return new Response(null, { status: 204 })
      return new Response('{}', { status: 404 })
    })
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    renderPage()

    await screen.findByText('abcd••••wxyz')
    await user.selectOptions(screen.getByLabelText('Сервис'), 'infura')
    await user.type(screen.getByLabelText('Название', { selector: '.add-key-panel input' }), 'Backup')
    await user.type(screen.getByLabelText('Значение API-ключа'), 'new-secret')
    await user.click(screen.getByRole('button', { name: 'Добавить API-ключ' }))
    expect(await screen.findByText('API-ключ добавлен, пул провайдеров обновлён.')).toBeInTheDocument()

    await user.click(screen.getByRole('checkbox', { name: 'Отключить Primary' }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      '/api/api-keys/7',
      expect.objectContaining({ method: 'PATCH', body: JSON.stringify({ enabled: false }) }),
    ))

    await user.click(screen.getByRole('button', { name: 'Изменить Primary' }))
    const replacement = screen.getByLabelText('Новое значение (необязательно)')
    await user.type(replacement, 'rotated-secret')
    await user.click(screen.getByRole('button', { name: 'Сохранить' }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      '/api/api-keys/7',
      expect.objectContaining({ method: 'PATCH', body: JSON.stringify({ value: 'rotated-secret' }) }),
    ))

    await user.click(screen.getByRole('button', { name: 'Удалить Primary' }))
    expect(screen.getByText('Удалить этот ключ без возможности восстановления?')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Удалить' }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      '/api/api-keys/7',
      expect.objectContaining({ method: 'DELETE' }),
    ))

    const postedBody = fetchMock.mock.calls
      .filter(([url, init]) => url === '/api/api-keys' && init?.method === 'POST')
      .map(([, init]) => JSON.parse(String(init?.body)))[0]
    expect(postedBody).toEqual({ service: 'infura', label: 'Backup', value: 'new-secret' })
  })
})
