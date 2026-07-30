import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { FilePlus2, Plus, Trash2, UploadCloud } from 'lucide-react'
import { useRef, useState } from 'react'
import {
  controlWalletJob,
  createWalletBatch,
  deleteWalletBatch,
  getWalletJobs,
  previewWalletSources,
} from '../../api/client'
import type { Job, WalletPreview } from '../../api/types'
import { ConfirmModal } from '../../components/ConfirmModal'
import { JobCard } from '../../components/JobCard'
import { EmptyState, ErrorState, LoadingState } from '../../components/States'
import { PreviewModal } from './PreviewModal'
import { JobLogsModal } from './JobLogsModal'
import {
  createManualSource,
  getAggregateStats,
  parseImportFile,
  type LocalImportSource,
} from './importHelpers'

export function WalletsPage() {
  const inputRef = useRef<HTMLInputElement>(null)
  const queryClient = useQueryClient()
  const [sources, setSources] = useState<LocalImportSource[]>([])
  const [manual, setManual] = useState('')
  const [importError, setImportError] = useState('')
  const [preview, setPreview] = useState<WalletPreview | null>(null)
  const [excludedAddresses, setExcludedAddresses] = useState<Set<string>>(new Set())
  const [batchName, setBatchName] = useState(`Пакет кошельков ${new Date().toLocaleDateString('ru-RU')}`)
  const [chain, setChain] = useState('ethereum')
  const [logJob, setLogJob] = useState<Job | null>(null)
  const [deleteJob, setDeleteJob] = useState<Job | null>(null)
  const stats = getAggregateStats(sources)

  const jobs = useQuery({
    queryKey: ['wallet-jobs'],
    queryFn: getWalletJobs,
    refetchInterval: (query) =>
      query.state.data?.some((job) => ['queued', 'running', 'cancelling'].includes(job.status)) ? 3000 : false,
  })

  const previewMutation = useMutation({
    mutationFn: () =>
      previewWalletSources(
        sources.flatMap((source) => (source.file ? [source.file] : [])),
        sources.filter((source) => source.kind === 'manual').map((source) => source.raw).join('\n'),
        chain,
      ),
    onSuccess: (result) => {
      setExcludedAddresses(new Set())
      setPreview(result)
    },
  })

  const createMutation = useMutation({
    mutationFn: () => createWalletBatch(
      preview?.token ?? '',
      batchName.trim(),
      chain,
      Array.from(excludedAddresses),
    ),
    onSuccess: () => {
      setPreview(null)
      setExcludedAddresses(new Set())
      setSources([])
      void queryClient.invalidateQueries({ queryKey: ['wallet-jobs'] })
    },
  })

  const controlMutation = useMutation({
    mutationFn: ({ id, action }: { id: string; action: 'stop' | 'resume' | 'retry' | 'recalculate' }) =>
      controlWalletJob(id, action),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['wallet-jobs'] }),
  })
  const deleteMutation = useMutation({
    mutationFn: deleteWalletBatch,
    onSuccess: () => {
      setDeleteJob(null)
      void queryClient.invalidateQueries({ queryKey: ['wallet-jobs'] })
    },
  })

  function deleteHistory(job: Job) {
    if (job.importId === null) return
    deleteMutation.reset()
    setDeleteJob(job)
  }

  function togglePreviewAddress(address: string) {
    setExcludedAddresses((current) => {
      const next = new Set(current)
      if (next.has(address)) next.delete(address)
      else next.add(address)
      return next
    })
  }

  async function addFiles(files: FileList | null) {
    if (!files?.length) return
    setImportError('')
    const results = await Promise.allSettled(Array.from(files).map(parseImportFile))
    const accepted = results.flatMap((result) => (result.status === 'fulfilled' ? [result.value] : []))
    const errors = results.flatMap((result) =>
      result.status === 'rejected' ? [String(result.reason instanceof Error ? result.reason.message : result.reason)] : [],
    )
    setSources((current) => [...current, ...accepted])
    setImportError(errors.join('. '))
    if (inputRef.current) inputRef.current.value = ''
  }

  function addManual() {
    const source = createManualSource(manual)
    if (!source) return
    setSources((current) => [...current, source])
    setManual('')
  }

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <span className="eyebrow">Ввод и обработка</span>
          <h1>Кошельки</h1>
          <p>Объедините файлы и вставленные адреса в один проверенный пакет для анализа.</p>
        </div>
      </header>

      <div className="wallet-layout">
        <section className="panel import-panel">
          <div className="section-heading">
            <div><h2>Список источников</h2><p>CSV/XLSX должны содержать только столбцы index и wallet_address.</p></div>
            {sources.length > 0 && (
              <button className="text-button danger" type="button" onClick={() => setSources([])}>
                Очистить всё
              </button>
            )}
          </div>
          <button className="dropzone" type="button" onClick={() => inputRef.current?.click()}>
            <UploadCloud size={28} />
            <strong>Выберите файлы CSV, XLSX или TXT</strong>
            <span>Можно загрузить несколько файлов разных форматов</span>
          </button>
          <input
            ref={inputRef}
            className="visually-hidden"
            type="file"
            accept=".csv,.xlsx,.txt"
            multiple
            onChange={(event) => void addFiles(event.target.files)}
          />
          <div className="divider"><span>или вставьте адреса</span></div>
          <label className="field">
            <span>Один адрес кошелька в строке</span>
            <textarea
              rows={5}
              value={manual}
              placeholder={'0x1234…\n0xabcd…'}
              onChange={(event) => setManual(event.target.value)}
            />
          </label>
          <button className="button secondary" type="button" disabled={!manual.trim()} onClick={addManual}>
            <Plus size={17} /> Добавить адреса
          </button>
          {importError && <p className="inline-error" role="alert">{importError}</p>}

          <div className="source-list">
            {sources.length === 0 ? (
              <EmptyState title="Источников пока нет" message="Загрузите файлы или добавьте адреса вручную." />
            ) : sources.map((source) => (
              <div className="source-item" key={source.id}>
                <span className="file-icon"><FilePlus2 size={18} /></span>
                <div>
                  <strong>{source.name}</strong>
                  <span>{source.addresses.length.toLocaleString('ru-RU')} корректных · {source.invalidCount} некорректных</span>
                </div>
                <span className="file-type">{source.kind === 'manual' ? 'вручную' : source.kind}</span>
                <button
                  className="icon-button"
                  type="button"
                  aria-label={`Удалить ${source.name}`}
                  onClick={() => setSources((current) => current.filter((item) => item.id !== source.id))}
                >
                  <Trash2 size={17} />
                </button>
              </div>
            ))}
          </div>
          {sources.length > 0 && (
            <div className="aggregate-bar">
              <div><strong>{stats.unique.toLocaleString('ru-RU')}</strong><span>уникальных</span></div>
              <div><strong>{stats.duplicates.toLocaleString('ru-RU')}</strong><span>дубликатов</span></div>
              <div><strong>{stats.invalid.toLocaleString('ru-RU')}</strong><span>некорректных</span></div>
              <button
                className="button primary"
                type="button"
                disabled={previewMutation.isPending}
                onClick={() => previewMutation.mutate()}
              >
                {previewMutation.isPending ? 'Проверка…' : 'Предпросмотр пакета'}
              </button>
            </div>
          )}
          {previewMutation.error && <p className="inline-error">{previewMutation.error.message}</p>}
        </section>

        <section className="panel jobs-panel">
          <div className="section-heading"><div><h2>История пакетов</h2><p>Текущий статус обработки и последние результаты.</p></div></div>
          {jobs.isLoading && <LoadingState label="Загрузка пакетов" />}
          {jobs.error && <ErrorState error={jobs.error} onRetry={() => void jobs.refetch()} />}
          {jobs.data?.length === 0 && <EmptyState title="Пакетов пока нет" message="Подтверждённые пакеты появятся здесь." />}
          <div className="job-list">
            {jobs.data?.map((job) => (
              <JobCard
                key={job.id}
                job={job}
                onAction={(action) => controlMutation.mutate({ id: job.id, action })}
                onViewLogs={() => setLogJob(job)}
                onDelete={
                  job.importId !== null && !['queued', 'running', 'cancelling'].includes(job.status)
                    ? () => deleteHistory(job)
                    : undefined
                }
              />
            ))}
          </div>
        </section>
      </div>
      <PreviewModal
        preview={preview}
        open={preview !== null}
        batchName={batchName}
        chain={chain}
        onBatchNameChange={setBatchName}
        onChainChange={setChain}
        onClose={() => {
          setPreview(null)
          setExcludedAddresses(new Set())
        }}
        onConfirm={() => createMutation.mutate()}
        excludedAddresses={excludedAddresses}
        onToggleAddress={togglePreviewAddress}
        onSelectAll={() => setExcludedAddresses(new Set())}
        onSelectOnlyNew={() => setExcludedAddresses(new Set(
          preview?.entries
            .filter((entry) => entry.alreadyAnalyzed)
            .map((entry) => entry.address) ?? [],
        ))}
        confirming={createMutation.isPending}
        confirmError={createMutation.error?.message}
      />
      <JobLogsModal job={logJob} onClose={() => setLogJob(null)} />
      <ConfirmModal
        open={deleteJob !== null}
        title="Удаление пакета из истории"
        message={
          deleteJob
            ? `Пакет «${deleteJob.name}», его задача и логи будут удалены. Собранные кошельки и признаки сохранятся.`
            : ''
        }
        confirming={deleteMutation.isPending}
        error={deleteMutation.error?.message}
        onConfirm={() => {
          if (deleteJob?.importId !== null && deleteJob?.importId !== undefined) {
            deleteMutation.mutate(deleteJob.importId)
          }
        }}
        onClose={() => {
          deleteMutation.reset()
          setDeleteJob(null)
        }}
      />
    </div>
  )
}
