import { useQuery } from '@tanstack/react-query'
import { useEffect, useRef } from 'react'
import { getLogs } from '../../api/client'
import type { Job } from '../../api/types'
import { Modal } from '../../components/Modal'


interface JobLogsModalProps {
  job: Job | null
  onClose: () => void
}


import { formatTime } from '../../utils/datetime'

function formatLineTime(value: string) {
  return formatTime(value)
}


export function JobLogsModal({ job, onClose }: JobLogsModalProps) {
  const terminalRef = useRef<HTMLDivElement>(null)
  const logs = useQuery({
    queryKey: ['job-logs', job?.id],
    queryFn: () => getLogs({
      page: 0,
      pageSize: 200,
      level: '',
      event: '',
      search: '',
      jobId: job?.id ?? '',
      clusterRunId: '',
    }),
    enabled: job !== null,
    refetchInterval: job && ['queued', 'running', 'cancelling'].includes(job.status) ? 2000 : false,
  })
  const items = [...(logs.data?.items ?? [])].reverse()

  useEffect(() => {
    const terminal = terminalRef.current
    if (terminal) terminal.scrollTop = terminal.scrollHeight
  }, [items.length])

  return (
    <Modal
      title={job ? `Логи: ${job.name}` : 'Логи пакета'}
      open={job !== null}
      onClose={onClose}
      footer={<button className="button secondary" type="button" onClick={onClose}>Закрыть</button>}
    >
      <div className="job-terminal" ref={terminalRef} role="log" aria-live="polite">
        {logs.isLoading && <span className="terminal-muted">Загрузка логов…</span>}
        {logs.error && <span className="terminal-error">{logs.error.message}</span>}
        {!logs.isLoading && !logs.error && items.length === 0 && (
          <span className="terminal-muted">Для этого пакета логов пока нет.</span>
        )}
        {items.map((log) => {
          const context = Object.keys(log.context).length ? ` ${JSON.stringify(log.context)}` : ''
          return (
            <div className={`terminal-line ${log.level.toLowerCase()}`} key={log.id}>
              <span>{formatLineTime(log.createdAt)}</span>
              <strong>{log.level.toUpperCase()}</strong>
              <code>{log.event}: {log.message}{context}</code>
            </div>
          )
        })}
      </div>
    </Modal>
  )
}
