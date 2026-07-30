import { CheckCircle2 } from 'lucide-react'
import type { WalletPreview } from '../../api/types'
import { Modal } from '../../components/Modal'

interface PreviewModalProps {
  preview: WalletPreview | null
  open: boolean
  batchName: string
  chain: string
  onBatchNameChange: (value: string) => void
  onChainChange: (value: string) => void
  onClose: () => void
  onConfirm: () => void
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
            disabled={confirming || !batchName.trim()}
            onClick={onConfirm}
          >
            <CheckCircle2 size={17} />
            {confirming ? 'Создание пакета…' : 'Подтвердить пакет'}
          </button>
        </>
      }
    >
      <div className="metric-grid">
        <div><strong>{preview.validCount.toLocaleString('ru-RU')}</strong><span>Корректных уникальных адресов</span></div>
        <div><strong>{preview.duplicateCount.toLocaleString('ru-RU')}</strong><span>Удалено дубликатов</span></div>
        <div><strong>{preview.invalidCount.toLocaleString('ru-RU')}</strong><span>Некорректных строк</span></div>
        <div><strong>{preview.sourceCount}</strong><span>Источников</span></div>
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
          <h3>Предпросмотр записей</h3>
          <div className="address-preview" tabIndex={0}>
            {preview.entries.map((entry) => (
              <div className="preview-entry" key={`${entry.source}-${entry.row}-${entry.address}`}>
                <code>{entry.checksumAddress}</code>
                <span>
                  {entry.source}, строка {entry.row}
                  {entry.sourceIndex !== null ? ` · index ${entry.sourceIndex}` : ''}
                </span>
              </div>
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
