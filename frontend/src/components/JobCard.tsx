import { CircleStop, Play, RotateCcw, ScrollText, Trash2 } from 'lucide-react'
import type { Job } from '../api/types'
import { formatEta, formatRelativeTime } from '../utils/jobFormatting'

interface JobCardProps {
  job: Job
  onAction?: (action: 'stop' | 'resume' | 'retry' | 'recalculate') => void
  selected?: boolean
  onSelect?: () => void
  onViewLogs?: () => void
  onDelete?: () => void
}

const statusLabels: Record<Job['status'], string> = {
  queued: 'В очереди',
  running: 'Выполняется',
  completed: 'Завершено',
  completed_with_errors: 'Завершено с ошибками',
  failed: 'Ошибка',
  cancelled: 'Отменено',
  cancelling: 'Отменяется',
}

export function JobCard({ job, onAction, selected, onSelect, onViewLogs, onDelete }: JobCardProps) {
  const canStop = job.status === 'running' || job.status === 'queued'
  const canResume = job.status === 'cancelled'
  const canRetry = job.status === 'failed' || job.status === 'completed_with_errors'
  const canRecalculate = job.status === 'completed' || job.status === 'completed_with_errors'
  return (
    <article
      className={`job-card${selected ? ' selected' : ''}`}
      onClick={onSelect}
      tabIndex={onSelect ? 0 : undefined}
      onKeyDown={(event) => {
        if (onSelect && (event.key === 'Enter' || event.key === ' ')) onSelect()
      }}
    >
      <div className="job-card-top">
        <div>
          <strong>{job.name}</strong>
          <span>{formatRelativeTime(job.createdAt)} · {job.addressCount.toLocaleString('ru-RU')} кошельков</span>
        </div>
        <span className={`status-badge ${job.status}`}>{statusLabels[job.status]}</span>
      </div>
      <div className="job-stage">
        <span>{job.stage}</span>
        <span>{Math.round(job.progress)}%</span>
      </div>
      <div className="progress-track" aria-label={`Выполнено ${Math.round(job.progress)} процентов`}>
        <span style={{ width: `${Math.min(100, Math.max(0, job.progress))}%` }} />
      </div>
      {job.error && <p className="job-error">{job.error}</p>}
      {(((canStop || canResume || canRetry || canRecalculate) && onAction) || onViewLogs || onDelete) && (
        <div className="job-actions">
          <span>{canStop ? `Осталось: ${formatEta(job.etaSeconds)}` : ''}</span>
          <div className="job-action-buttons">
            {canStop && onAction && (
              <button type="button" className="text-button danger" onClick={(event) => {
                event.stopPropagation()
                onAction('stop')
              }}>
                <CircleStop size={15} /> Остановить
              </button>
            )}
            {canResume && onAction && (
              <button type="button" className="text-button" onClick={(event) => {
                event.stopPropagation()
                onAction('resume')
              }}>
                <Play size={15} /> Продолжить
              </button>
            )}
            {canRetry && onAction && (
              <button type="button" className="text-button" onClick={(event) => {
                event.stopPropagation()
                onAction('retry')
              }}>
                <RotateCcw size={15} /> Повторить
              </button>
            )}
            {canRecalculate && onAction && (
              <button type="button" className="text-button" onClick={(event) => {
                event.stopPropagation()
                onAction('recalculate')
              }}>
                <RotateCcw size={15} /> Пересчитать
              </button>
            )}
            {onViewLogs && (
              <button type="button" className="text-button" onClick={(event) => {
                event.stopPropagation()
                onViewLogs()
              }}>
                <ScrollText size={15} /> Логи
              </button>
            )}
            {onDelete && (
              <button type="button" className="text-button danger" onClick={(event) => {
                event.stopPropagation()
                onDelete()
              }}>
                <Trash2 size={15} /> Удалить
              </button>
            )}
          </div>
        </div>
      )}
    </article>
  )
}
