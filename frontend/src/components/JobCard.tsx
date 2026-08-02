import { CircleStop, ClipboardList, Play, RotateCcw, ScrollText, Trash2 } from 'lucide-react'
import type { Job } from '../api/types'
import { formatRelativeTime } from '../utils/jobFormatting'

interface JobCardProps {
  job: Job
  onAction?: (action: 'stop' | 'resume' | 'retry' | 'recalculate') => void
  selected?: boolean
  onSelect?: () => void
  onViewLogs?: () => void
  onViewSummary?: () => void
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

export function JobCard({
  job,
  onAction,
  selected,
  onSelect,
  onViewLogs,
  onViewSummary,
  onDelete,
}: JobCardProps) {
  const canStop = job.status === 'running' || job.status === 'queued'
  const canResume = job.status === 'cancelled'
  const canRetry = job.status === 'failed' || job.status === 'completed_with_errors'
  const canRecalculate = job.status === 'completed' || job.status === 'completed_with_errors'
  const doneLabel = `${job.progressDone.toLocaleString('ru-RU')} / ${job.progressTotal.toLocaleString('ru-RU')}`
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
        <span>{doneLabel} кошельков</span>
        <span>{Math.round(job.progress)}%</span>
      </div>
      <div className="progress-track" aria-label={`Обработано ${doneLabel} кошельков`}>
        <span style={{ width: `${Math.min(100, Math.max(0, job.progress))}%` }} />
      </div>
      {job.error && <p className="job-error">{job.error}</p>}
      {(((canStop || canResume || canRetry || canRecalculate) && onAction) || onViewLogs || onViewSummary || onDelete) && (
        <div className="job-actions">
          <span>{job.stage}</span>
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
            {onViewSummary && (
              <button type="button" className="text-button" onClick={(event) => {
                event.stopPropagation()
                onViewSummary()
              }}>
                <ClipboardList size={15} /> Сводка
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
