import { AlertTriangle, Inbox, LoaderCircle } from 'lucide-react'

export function LoadingState({ label = 'Загрузка данных' }: { label?: string }) {
  return (
    <div className="state-block" role="status">
      <LoaderCircle className="spin" size={22} />
      <span>{label}</span>
    </div>
  )
}

export function EmptyState({ title, message }: { title: string; message: string }) {
  return (
    <div className="state-block">
      <Inbox size={24} />
      <strong>{title}</strong>
      <span>{message}</span>
    </div>
  )
}

export function ErrorState({ error, onRetry }: { error: Error; onRetry?: () => void }) {
  return (
    <div className="state-block error-state" role="alert">
      <AlertTriangle size={24} />
      <strong>Произошла ошибка</strong>
      <span>{error.message}</span>
      {onRetry && (
        <button className="button secondary small" type="button" onClick={onRetry}>
          Повторить
        </button>
      )}
    </div>
  )
}
