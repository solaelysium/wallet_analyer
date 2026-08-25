import { CheckCircle2 } from 'lucide-react'
import type { WalletPreview } from '../../api/types'
import { Modal } from '../../components/Modal'
import { formatDateTime } from '../../utils/datetime'

interface PreviewModalProps {
  preview: WalletPreview | null
  open: boolean
  batchName: string
  chain: string
  onBatchNameChange: (value: string) => void
  onChainChange: (value: string) => void
  onClose: () => void
  onConfirm: () => void
  excludedAddresses: Set<string>
  onToggleAddress: (address: string) => void
  onSelectAll: () => void
  onSelectOnlyNew: () => void
  confirming: boolean
  confirmError?: string
}

export function PreviewModal({
  preview,
  open,
  batchName,
  chain,
  onBatchNameChange,
  onChainChange,
  onClose,
  onConfirm,
  excludedAddresses,
  onToggleAddress,
  onSelectAll,
  onSelectOnlyNew,
  confirming,
  confirmError,
}: PreviewModalProps) {
  if (!preview) return null
  const sourceCounts = new Map<string, { valid: number; issues: number }>()
  preview.entries.forEach((entry) => {
    const counts = sourceCounts.get(entry.source) ?? { valid: 0, issues: 0 }
    counts.valid += 1
    sourceCounts.set(entry.source, counts)
  })
  preview.issues.forEach((issue) => {
    const counts = sourceCounts.get(issue.source) ?? { valid: 0, issues: 0 }
    counts.issues += 1
    sourceCounts.set(issue.source, counts)
  })
  const selectedCount = preview.entries.length - excludedAddresses.size

  function analysisDate(value: string | null) {
    if (!value) return ''
    return formatDateTime(value)
  }

  function analysisStatusLabel(status: string | null) {
    if (status === 'completed') return 'успешно'
    if (status === 'skipped') return 'пропущено'
    if (status === 'failed') return 'с ошибкой'
    if (status === 'cancelled') return 'отменено'
    if (status === 'running') return 'в работе'
    if (status === 'queued') return 'в очереди'
    return status
  }

  function analysisStatusTone(status: string | null) {
    if (status === 'completed') return 'success'
    if (status === 'skipped') return 'warning'
    if (status === 'failed') return 'danger'
    if (status === 'cancelled') return 'muted'
    return 'existing'
  }

  function priorAnalysisText(entry: WalletPreview['entries'][number]) {
    if (!entry.alreadyAnalyzed) return 'Ранее не проверялся'
    const when = analysisDate(entry.lastAnalyzedAt)
    const status = analysisStatusLabel(entry.lastAnalysisStatus)
    const base = when
      ? (status ? `Проверен ${when} · ${status}` : `Проверен ${when}`)
      : (status ? `Уже проверялся · ${status}` : 'Уже проверялся ранее')
    if (
      entry.lastAnalysisError
      && (entry.lastAnalysisStatus === 'failed' || entry.lastAnalysisStatus === 'skipped')
    ) {
      return `${base}: ${entry.lastAnalysisError}`
    }
    return base
  }

  return (
    <Modal
      title="Проверка пакета кошельков"
      open={open}
      onClose={onClose}
      footer={
        <>
          <button className="button secondary" type="button" onClick={onClose}>Назад</button>
          <button
            className="button primary"
            type="button"
            disabled={confirming || !batchName.trim() || selectedCount === 0}
            onClick={onConfirm}
          >
            <CheckCircle2 size={17} />
            {confirming ? 'Создание пакета…' : `Запустить анализ (${selectedCount})`}
          </button>
        </>
      }
    >
      <div className="metric-grid">
        <div><strong>{selectedCount.toLocaleString('ru-RU')} / {preview.validCount.toLocaleString('ru-RU')}</strong><span>Выбрано для анализа</span></div>
        <div><strong>{preview.analyzedCount.toLocaleString('ru-RU')}</strong><span>Уже проверялись ранее</span></div>
        <div><strong>{preview.duplicateCount.toLocaleString('ru-RU')}</strong><span>Удалено дубликатов</span></div>
        <div><strong>{preview.invalidCount.toLocaleString('ru-RU')}</strong><span>Некорректных строк</span></div>
      </div>
      <div className="parameter-grid">
        <label className="field">
          <span>Название пакета</span>
          <input value={batchName} onChange={(event) => onBatchNameChange(event.target.value)} />
        </label>
        <label className="field">
          <span>Сеть</span>
          <select value={chain} onChange={(event) => onChainChange(event.target.value)}>
            <option value="ethereum">Ethereum</option>
          </select>
        </label>
      </div>
      <div className="preview-columns">
        <section>
          <h3>Количество по источникам</h3>
          <div className="compact-list">
            {Array.from(sourceCounts).map(([source, counts]) => (
              <div key={source}>
                <span>{source}</span>
                <strong>{counts.valid} корректных · {counts.issues} проблем</strong>
              </div>
            ))}
          </div>
        </section>
        <section>
          <div className="preview-list-heading">
            <h3>Предпросмотр записей</h3>
            <div>
              <button className="text-button" type="button" onClick={onSelectAll}>Выбрать все</button>
              <button className="text-button" type="button" onClick={onSelectOnlyNew}>Только новые</button>
            </div>
          </div>
          <div className="address-preview" tabIndex={0}>
            {preview.entries.map((entry) => (
              <label
                className={`preview-entry${excludedAddresses.has(entry.address) ? ' excluded' : ''}`}
                key={`${entry.source}-${entry.row}-${entry.address}`}
              >
                <input
                  type="checkbox"
                  checked={!excludedAddresses.has(entry.address)}
                  onChange={() => onToggleAddress(entry.address)}
                />
                <div>
                  <code>{entry.checksumAddress}</code>
                  <span>
                    {entry.source}, строка {entry.row}
                    {entry.sourceIndex !== null ? ` · index ${entry.sourceIndex}` : ''}
                  </span>
                  <span
                    className={
                      entry.alreadyAnalyzed
                        ? `analysis-status ${analysisStatusTone(entry.lastAnalysisStatus)}`
                        : 'analysis-status'
                    }
                  >
                    {priorAnalysisText(entry)}
                  </span>
                </div>
              </label>
            ))}
          </div>
        </section>
      </div>
      {preview.issues.length > 0 && (
        <details>
          <summary>Показать проблемы импорта ({preview.issues.length})</summary>
          <div className="invalid-list">
            {preview.issues.map((issue, index) => (
              <div key={`${issue.source}-${issue.row}-${index}`}>
                <strong>{issue.kind === 'duplicate' ? 'дубликат' : 'некорректный адрес'} · {issue.source}, строка {issue.row}</strong>
                <span>{issue.value || '(пусто)'} — {issue.detail}</span>
              </div>
            ))}
          </div>
        </details>
      )}
      {confirmError && <p className="inline-error" role="alert">{confirmError}</p>}
    </Modal>
  )
}
