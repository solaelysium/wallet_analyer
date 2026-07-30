import { AlertTriangle } from 'lucide-react'
import { Modal } from './Modal'


interface ConfirmModalProps {
  open: boolean
  title: string
  message: string
  confirmLabel?: string
  confirming?: boolean
  error?: string
  onConfirm: () => void
  onClose: () => void
}


export function ConfirmModal({
  open,
  title,
  message,
  confirmLabel = 'Удалить',
  confirming = false,
  error,
  onConfirm,
  onClose,
}: ConfirmModalProps) {
  return (
    <Modal
      title={title}
      open={open}
      onClose={confirming ? () => undefined : onClose}
      footer={
        <>
          <button className="button secondary" type="button" disabled={confirming} onClick={onClose}>
            Отмена
          </button>
          <button className="button danger" type="button" disabled={confirming} onClick={onConfirm}>
            {confirming ? 'Удаление…' : confirmLabel}
          </button>
        </>
      }
    >
      <div className="confirmation-message">
        <span className="confirmation-icon"><AlertTriangle size={22} /></span>
        <div>
          <strong>Действие нельзя отменить</strong>
          <p>{message}</p>
        </div>
      </div>
      {error && <p className="inline-error" role="alert">{error}</p>}
    </Modal>
  )
}
