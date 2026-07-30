import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  CheckCircle2,
  KeyRound,
  Pencil,
  Plus,
  RefreshCw,
  Save,
  Settings2,
  Trash2,
  X,
} from 'lucide-react'
import { useEffect, useState } from 'react'
import {
  createApiKey,
  deleteApiKey,
  getApiKeys,
  getRuntimeSettings,
  updateApiKey,
  updateRuntimeSettings,
} from '../../api/client'
import type {
  ApiKeyCreate,
  ApiKeyRecord,
  ApiKeyUpdate,
  ProviderService,
  RuntimeSettingsUpdate,
} from '../../api/types'
import { EmptyState, ErrorState, LoadingState } from '../../components/States'

const services: { id: ProviderService; label: string; purpose: string }[] = [
  { id: 'etherscan', label: 'Etherscan', purpose: 'Транзакции и переводы токенов' },
  { id: 'infura', label: 'Infura', purpose: 'Доступ к Ethereum RPC' },
  { id: 'coingecko', label: 'CoinGecko', purpose: 'Данные о ценах токенов' },
]

const emptyKey: ApiKeyCreate = { service: 'etherscan', label: '', value: '' }

function dateLabel(value: string | null) {
  if (!value) return 'Никогда'
  return new Date(value).toLocaleString('ru-RU')
}

function KeyRow({
  apiKey,
  busy,
  onUpdate,
  onDelete,
}: {
  apiKey: ApiKeyRecord
  busy: boolean
  onUpdate: (values: ApiKeyUpdate) => void
  onDelete: () => void
}) {
  const [editing, setEditing] = useState(false)
  const [confirmingDelete, setConfirmingDelete] = useState(false)
  const [label, setLabel] = useState(apiKey.label)
  const [value, setValue] = useState('')

  function save() {
    const update: ApiKeyUpdate = {}
    if (label.trim() !== apiKey.label) update.label = label.trim()
    if (value.trim()) update.value = value.trim()
    if (Object.keys(update).length) onUpdate(update)
    setValue('')
    setEditing(false)
  }

  return (
    <article className={apiKey.enabled ? 'api-key-row' : 'api-key-row disabled'}>
      <div className="key-identity">
        <span className={`key-service ${apiKey.service}`}>{apiKey.service}</span>
        <div>
          <strong>{apiKey.label}</strong>
          <code>{apiKey.maskedValue}</code>
        </div>
      </div>
      <div className="key-metadata">
        <span>Последнее использование</span>
        <strong>{dateLabel(apiKey.lastUsedAt)}</strong>
        {apiKey.errorCount > 0 && <small>Ошибок провайдера: {apiKey.errorCount}</small>}
      </div>
      <label className="toggle-field">
        <input
          type="checkbox"
          checked={apiKey.enabled}
          disabled={busy}
          aria-label={`${apiKey.enabled ? 'Отключить' : 'Включить'} ${apiKey.label}`}
          onChange={() => onUpdate({ enabled: !apiKey.enabled })}
        />
        <span aria-hidden="true" />
        {apiKey.enabled ? 'Включён' : 'Отключён'}
      </label>
      <div className="key-actions">
        <button
          className="icon-button"
          type="button"
          aria-label={`Изменить ${apiKey.label}`}
          disabled={busy}
          onClick={() => {
            setConfirmingDelete(false)
            setEditing((current) => !current)
          }}
        >
          <Pencil size={16} />
        </button>
        <button
          className="icon-button danger"
          type="button"
          aria-label={`Удалить ${apiKey.label}`}
          disabled={busy}
          onClick={() => {
            setEditing(false)
            setConfirmingDelete(true)
          }}
        >
          <Trash2 size={16} />
        </button>
      </div>
      {editing && (
        <div className="key-editor">
          <label className="field">
            <span>Название</span>
            <input value={label} maxLength={128} onChange={(event) => setLabel(event.target.value)} />
          </label>
          <label className="field">
            <span>Новое значение (необязательно)</span>
            <input
              type="password"
              value={value}
              autoComplete="new-password"
              placeholder="Оставьте пустым, чтобы сохранить текущее"
              onChange={(event) => setValue(event.target.value)}
            />
          </label>
          <div className="inline-actions">
            <button className="button primary small" type="button" disabled={!label.trim() || busy} onClick={save}>
              <Save size={14} /> Сохранить
            </button>
            <button className="button secondary small" type="button" onClick={() => setEditing(false)}>
              Отмена
            </button>
          </div>
        </div>
      )}
      {confirmingDelete && (
        <div className="delete-confirmation" role="alert">
          <span>Удалить этот ключ без возможности восстановления?</span>
          <div className="inline-actions">
            <button className="button danger small" type="button" disabled={busy} onClick={onDelete}>
              Удалить
            </button>
            <button className="button secondary small" type="button" onClick={() => setConfirmingDelete(false)}>
              Оставить ключ
            </button>
          </div>
        </div>
      )}
    </article>
  )
}

export function SettingsPage() {
  const queryClient = useQueryClient()
  const [runtimeValues, setRuntimeValues] = useState<RuntimeSettingsUpdate | null>(null)
  const [newKey, setNewKey] = useState<ApiKeyCreate>(emptyKey)
  const [resultMessage, setResultMessage] = useState('')

  const settings = useQuery({ queryKey: ['runtime-settings'], queryFn: getRuntimeSettings })
  const apiKeys = useQuery({ queryKey: ['api-keys'], queryFn: getApiKeys })

  useEffect(() => {
    if (!settings.data) return
    const { provider_health: _health, message: _message, ...values } = settings.data
    setRuntimeValues(values)
  }, [settings.data])

  async function refreshSettings() {
    await Promise.all([settings.refetch(), apiKeys.refetch()])
    setResultMessage('Статус провайдеров обновлён.')
  }

  const settingsMutation = useMutation({
    mutationFn: (values: RuntimeSettingsUpdate) => updateRuntimeSettings(values),
    onSuccess: (data) => {
      queryClient.setQueryData(['runtime-settings'], data)
      setResultMessage(data.message ?? 'Настройки применены.')
    },
  })

  const createMutation = useMutation({
    mutationFn: createApiKey,
    onSuccess: async () => {
      setNewKey(emptyKey)
      setResultMessage('API-ключ добавлен, пул провайдеров обновлён.')
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['api-keys'] }),
        queryClient.invalidateQueries({ queryKey: ['runtime-settings'] }),
      ])
    },
  })

  const updateMutation = useMutation({
    mutationFn: ({ id, values }: { id: number; values: ApiKeyUpdate }) => updateApiKey(id, values),
    onSuccess: async () => {
      setResultMessage('API-ключ обновлён, пул провайдеров обновлён.')
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['api-keys'] }),
        queryClient.invalidateQueries({ queryKey: ['runtime-settings'] }),
      ])
    },
  })

  const deleteMutation = useMutation({
    mutationFn: deleteApiKey,
    onSuccess: async () => {
      setResultMessage('API-ключ удалён из пула провайдеров.')
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['api-keys'] }),
        queryClient.invalidateQueries({ queryKey: ['runtime-settings'] }),
      ])
    },
  })

  const mutationError =
    settingsMutation.error ?? createMutation.error ?? updateMutation.error ?? deleteMutation.error

  function setNumber(name: keyof RuntimeSettingsUpdate, value: string) {
    setRuntimeValues((current) => current ? { ...current, [name]: Number(value) } : current)
  }

  return (
    <div className="page settings-page">
      <header className="page-header">
        <div>
          <span className="eyebrow">Конфигурация приложения</span>
          <h1>Настройки</h1>
          <p>Управляйте лимитами провайдеров, выполнением задач и API-ключами.</p>
        </div>
        <button className="button secondary" type="button" onClick={() => void refreshSettings()}>
          <RefreshCw size={16} /> Обновить статус
        </button>
      </header>

      {resultMessage && (
        <div className="result-message" role="status">
          <CheckCircle2 size={17} />
          <span>{resultMessage}</span>
          <button type="button" aria-label="Закрыть сообщение" onClick={() => setResultMessage('')}>
            <X size={14} />
          </button>
        </div>
      )}
      {mutationError && <p className="inline-error" role="alert">{mutationError.message}</p>}

      <section className="provider-health-grid" aria-label="Состояние провайдеров">
        {services.map((service) => {
          const health = settings.data?.provider_health.find((item) => item.service === service.id)
          return (
            <article className="panel provider-card" key={service.id}>
              <div>
                <span className={`provider-indicator ${health?.status ?? 'unavailable'}`} />
                <strong>{service.label}</strong>
              </div>
              <p>{service.purpose}</p>
              <span className={`provider-status ${health?.status ?? 'unavailable'}`}>
                {health?.status === 'ready' ? 'Готов' : health?.status === 'degraded' ? 'Ограничен' : 'Недоступен'}
              </span>
              <small>
                {health
                  ? `Работают ключи: ${health.healthyKeys}/${health.enabledKeys}`
                  : 'Ожидание статуса'}
              </small>
              {health?.message && <small>{health.message}</small>}
            </article>
          )
        })}
      </section>

      <div className="settings-layout">
        <section className="panel runtime-settings-panel">
          <div className="section-heading">
            <div><h2><Settings2 size={18} /> Рабочие настройки</h2><p>Изменения применяются без перезапуска приложения.</p></div>
          </div>
          {settings.isLoading && <LoadingState label="Загрузка настроек" />}
          {settings.error && <ErrorState error={settings.error} onRetry={() => void settings.refetch()} />}
          {runtimeValues && (
            <form
              onSubmit={(event) => {
                event.preventDefault()
                settingsMutation.mutate(runtimeValues)
              }}
            >
              <h3>Выполнение задач</h3>
              <div className="settings-form-grid">
                <label className="field">
                  <span>Обработчики задач</span>
                  <input type="number" min="1" step="1" required value={runtimeValues.job_workers} onChange={(event) => setNumber('job_workers', event.target.value)} />
                </label>
                <label className="field">
                  <span>Параллельность ключей</span>
                  <input type="number" min="1" step="1" required value={runtimeValues.key_concurrency} onChange={(event) => setNumber('key_concurrency', event.target.value)} />
                </label>
              </div>
              <h3>Работа провайдеров</h3>
              <div className="settings-form-grid">
                <label className="field">
                  <span>Тайм-аут (секунды)</span>
                  <input type="number" min="0.1" step="0.1" required value={runtimeValues.provider_timeout_seconds} onChange={(event) => setNumber('provider_timeout_seconds', event.target.value)} />
                </label>
                <label className="field">
                  <span>Максимум повторов</span>
                  <input type="number" min="1" step="1" required value={runtimeValues.provider_max_retries} onChange={(event) => setNumber('provider_max_retries', event.target.value)} />
                </label>
                <label className="field">
                  <span>Пауза (секунды)</span>
                  <input type="number" min="0" step="0.1" required value={runtimeValues.provider_cooldown_seconds} onChange={(event) => setNumber('provider_cooldown_seconds', event.target.value)} />
                </label>
              </div>
              <h3>Ограничения частоты</h3>
              <div className="settings-form-grid three">
                {services.map((service) => {
                  const name = `${service.id}_rps` as 'etherscan_rps' | 'infura_rps' | 'coingecko_rps'
                  return (
                    <label className="field" key={service.id}>
                      <span>{service.label}: запросов в секунду</span>
                      <input type="number" min="0.01" step="0.01" required value={runtimeValues[name]} onChange={(event) => setNumber(name, event.target.value)} />
                    </label>
                  )
                })}
              </div>
              <button className="button primary" type="submit" disabled={settingsMutation.isPending}>
                <Save size={16} /> {settingsMutation.isPending ? 'Применение…' : 'Применить настройки'}
              </button>
            </form>
          )}
        </section>

        <section className="panel add-key-panel">
          <div className="section-heading">
            <div><h2><Plus size={18} /> Добавить API-ключ</h2><p>Секрет отправляется один раз и больше не отображается.</p></div>
          </div>
          <form
            onSubmit={(event) => {
              event.preventDefault()
              createMutation.mutate({
                service: newKey.service,
                label: newKey.label.trim(),
                value: newKey.value.trim(),
              })
            }}
          >
            <label className="field">
              <span>Сервис</span>
              <select value={newKey.service} onChange={(event) => setNewKey((current) => ({ ...current, service: event.target.value as ProviderService }))}>
                {services.map((service) => <option value={service.id} key={service.id}>{service.label}</option>)}
              </select>
            </label>
            <label className="field">
              <span>Название</span>
              <input required maxLength={128} placeholder="Основной ключ" value={newKey.label} onChange={(event) => setNewKey((current) => ({ ...current, label: event.target.value }))} />
            </label>
            <label className="field">
              <span>Значение API-ключа</span>
              <input required type="password" autoComplete="new-password" placeholder="Вставьте секретное значение" value={newKey.value} onChange={(event) => setNewKey((current) => ({ ...current, value: event.target.value }))} />
            </label>
            <button className="button primary wide" type="submit" disabled={createMutation.isPending}>
              <KeyRound size={16} /> {createMutation.isPending ? 'Добавление…' : 'Добавить API-ключ'}
            </button>
          </form>
        </section>
      </div>

      <section className="panel api-keys-panel">
        <div className="section-heading">
          <div><h2><KeyRound size={18} /> API-ключи</h2><p>Секреты скрыты. Укажите новое значение для ротации ключа.</p></div>
          <span className="key-count">Ключей: {apiKeys.data?.length ?? 0}</span>
        </div>
        {apiKeys.isLoading && <LoadingState label="Загрузка API-ключей" />}
        {apiKeys.error && <ErrorState error={apiKeys.error} onRetry={() => void apiKeys.refetch()} />}
        {apiKeys.data?.length === 0 && <EmptyState title="Нет API-ключей" message="Добавьте ключ провайдера, чтобы начать сбор данных." />}
        <div className="api-key-list">
          {apiKeys.data?.map((apiKey) => (
            <KeyRow
              key={apiKey.id}
              apiKey={apiKey}
              busy={updateMutation.isPending || deleteMutation.isPending}
              onUpdate={(values) => updateMutation.mutate({ id: apiKey.id, values })}
              onDelete={() => deleteMutation.mutate(apiKey.id)}
            />
          ))}
        </div>
      </section>
    </div>
  )
}
