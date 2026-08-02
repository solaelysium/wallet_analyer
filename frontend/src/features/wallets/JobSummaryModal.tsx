import { useQuery } from '@tanstack/react-query'
import { getJobSummary } from '../../api/client'
import type { Job, JobSummary, JobSummaryWallet } from '../../api/types'
import { Modal } from '../../components/Modal'

interface JobSummaryModalProps {
  job: Job | null
  onClose: () => void
}

const metricRows: {
  key: keyof Pick<JobSummary, 'completed' | 'skipped' | 'failed' | 'cancelled' | 'running' | 'queued'>
  label: string
}[] = [
  { key: 'completed', label: 'Собрано' },
  { key: 'skipped', label: 'Пропущено' },
  { key: 'failed', label: 'Ошибки' },
  { key: 'cancelled', label: 'Отменено' },
  { key: 'running', label: 'В работе' },
  { key: 'queued', label: 'В очереди' },
]

function statusMeta(state: string): { label: string; tone: string } {
  if (state === 'completed') return { label: 'Успешно', tone: 'success' }
  if (state === 'skipped') return { label: 'Пропущено', tone: 'warning' }
  if (state === 'failed') return { label: 'Ошибка', tone: 'danger' }
  if (state === 'cancelled') return { label: 'Отменено', tone: 'muted' }
  if (state === 'running') return { label: 'В работе', tone: 'info' }
  if (state === 'queued') return { label: 'В очереди', tone: 'muted' }
  return { label: state, tone: 'muted' }
}

function reasonFor(wallet: JobSummaryWallet) {
  if (wallet.state === 'completed' || wallet.state === 'queued' || wallet.state === 'running') {
    return null
  }
  return wallet.error?.trim() || null
}

function txLabel(count: number | null) {
  if (count === null) return '—'
  return `${count.toLocaleString('ru-RU')} транз.`
}

export function JobSummaryModal({ job, onClose }: JobSummaryModalProps) {
  const summary = useQuery({
    queryKey: ['job-summary', job?.id],
    queryFn: () => getJobSummary(job!.id),
    enabled: job !== null,
    refetchInterval: job && ['queued', 'running', 'cancelling'].includes(job.status) ? 3000 : false,
  })

  return (
    <Modal
      title={job ? `Сводка: ${job.name}` : 'Сводка пакета'}
      open={job !== null}
      onClose={onClose}
      footer={<button className="button secondary" type="button" onClick={onClose}>Закрыть</button>}
    >
      {summary.isLoading && <p className="muted">Загрузка сводки…</p>}
      {summary.error && <p className="form-error">{summary.error.message}</p>}
      {summary.data && (
        <div className="job-summary">
          <p className="job-summary-total">
            Всего кошельков: <strong>{summary.data.total.toLocaleString('ru-RU')}</strong>
          </p>
          <dl className="job-summary-grid">
            {metricRows.map(({ key, label }) => (
              <div key={key}>
                <dt>{label}</dt>
                <dd>{summary.data[key].toLocaleString('ru-RU')}</dd>
              </div>
            ))}
          </dl>
          <div className="job-summary-list-panel">
            <div className="job-summary-list-head">
              <span>Кошелёк</span>
              <span>Транзакции</span>
              <span>Статус</span>
            </div>
            <div className="job-summary-list" role="list">
              {summary.data.wallets.length === 0 && (
                <p className="muted">В этом пакете пока нет кошельков.</p>
              )}
              {summary.data.wallets.map((wallet) => {
                const status = statusMeta(wallet.state)
                const reason = reasonFor(wallet)
                return (
                  <div className="job-summary-row" role="listitem" key={wallet.id}>
                    <code className="job-summary-address">{wallet.address}</code>
                    <span className="job-summary-tx">{txLabel(wallet.eventCount)}</span>
                    <div className="job-summary-status-block">
                      <span className={`job-summary-status ${status.tone}`}>{status.label}</span>
                      {reason && <span className="job-summary-reason">{reason}</span>}
                    </div>
                  </div>
                )
              })}
            </div>
          </div>
        </div>
      )}
    </Modal>
  )
}
