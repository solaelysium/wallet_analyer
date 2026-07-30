import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Download, Play, SlidersHorizontal } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import {
  exportAssignments,
  getClusteringJobs,
  getCurrentFeatureDataset,
  startClustering,
  stopClustering,
} from '../../api/client'
import type { ClusteringRequest } from '../../api/types'
import { JobCard } from '../../components/JobCard'
import { EmptyState, ErrorState, LoadingState } from '../../components/States'
import { downloadBlob } from '../features/formatters'
import { ClusterPlot } from './ClusterPlot'
import { validateClustering } from './validation'

const defaultRequest: ClusteringRequest = {
  algorithm: 'hdbscan',
  reducer: 'umap',
  feature_version: '',
  feature_names: [],
  n_clusters: 8,
  min_cluster_size: 20,
  min_samples: 5,
  random_state: 42,
  umap_neighbors: 15,
  umap_min_dist: 0.1,
  umap_metric: 'cosine',
  reducer_components: 5,
  scaler: 'robust',
  winsorize: true,
  winsor_lower: 0.01,
  winsor_upper: 0.99,
  log_transform: false,
  cluster_selection_method: 'eom',
}

export function ClusteringPage() {
  const queryClient = useQueryClient()
  const [request, setRequest] = useState(defaultRequest)
  const [selectedJobId, setSelectedJobId] = useState('')
  const [submitted, setSubmitted] = useState(false)
  const dataset = useQuery({
    queryKey: ['current-feature-dataset'],
    queryFn: getCurrentFeatureDataset,
  })
  const jobs = useQuery({
    queryKey: ['clustering-jobs'],
    queryFn: getClusteringJobs,
    refetchInterval: (query) =>
      query.state.data?.some((job) => ['queued', 'running', 'cancelling'].includes(job.status))
        ? 3000
        : false,
  })

  useEffect(() => {
    if (dataset.data && !request.feature_version) {
      setRequest((current) => ({ ...current, feature_version: dataset.data?.version ?? '' }))
    }
  }, [dataset.data, request.feature_version])

  useEffect(() => {
    if (!selectedJobId && jobs.data?.length) setSelectedJobId(jobs.data[0].id)
  }, [jobs.data, selectedJobId])

  const errors = useMemo(() => validateClustering(request), [request])
  const selectedJob = jobs.data?.find((job) => job.id === selectedJobId)

  const startMutation = useMutation({
    mutationFn: () => startClustering(request),
    onSuccess: (job) => {
      setSelectedJobId(job.id)
      void queryClient.invalidateQueries({ queryKey: ['clustering-jobs'] })
    },
  })
  const stopMutation = useMutation({
    mutationFn: stopClustering,
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['clustering-jobs'] }),
  })

  function toggleFeature(feature: string) {
    setRequest((current) => ({
      ...current,
      feature_names: current.feature_names.includes(feature)
        ? current.feature_names.filter((item) => item !== feature)
        : [...current.feature_names, feature],
    }))
  }

  async function runExport(format: 'csv' | 'xlsx') {
    if (!selectedJob) return
    const blob = await exportAssignments(selectedJob.id, format)
    downloadBlob(blob, `${selectedJob.name}-assignments.${format}`)
  }

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <span className="eyebrow">Рабочая область модели</span>
          <h1>Кластеризация</h1>
          <p>Снижайте размерность признаков, находите группы и изучайте профили кластеров.</p>
        </div>
        {selectedJob?.status === 'completed' && (
          <div className="export-actions">
            <button className="button secondary" type="button" onClick={() => void runExport('csv')}>
              <Download size={17} /> Распределение CSV
            </button>
            <button className="button secondary" type="button" onClick={() => void runExport('xlsx')}>
              <Download size={17} /> XLSX
            </button>
          </div>
        )}
      </header>

      <div className="clustering-layout">
        <aside className="panel settings-panel">
          <div className="section-heading">
            <div>
              <h2><SlidersHorizontal size={18} /> Конфигурация</h2>
              <p>Выберите набор признаков и параметры модели.</p>
            </div>
          </div>
          {dataset.isLoading && <LoadingState label="Загрузка метаданных признаков" />}
          {dataset.error && <ErrorState error={dataset.error} onRetry={() => void dataset.refetch()} />}
          {!dataset.isLoading && !dataset.error && !dataset.data && (
            <EmptyState title="Нет набора признаков" message="Обработайте кошельки перед запуском кластеризации." />
          )}
          {dataset.data && (
            <>
              <label className="field">
                <span>Текущий набор данных</span>
                <input value={`${dataset.data.name} (${dataset.data.rowCount.toLocaleString('ru-RU')} строк)`} readOnly />
              </label>
              <fieldset className="feature-selector">
                <legend>Числовые признаки <span>выбрано: {request.feature_names.length}</span></legend>
                <div>
                  {dataset.data.numericFeatures.map((feature) => (
                    <label key={feature}>
                      <input
                        type="checkbox"
                        checked={request.feature_names.includes(feature)}
                        onChange={() => toggleFeature(feature)}
                      />
                      {feature}
                    </label>
                  ))}
                </div>
              </fieldset>
              <div className="segmented-field">
                <span>Снижение размерности</span>
                <div>
                  {(['pca', 'umap'] as const).map((value) => (
                    <button
                      className={request.reducer === value ? 'active' : ''}
                      type="button"
                      key={value}
                      onClick={() => setRequest((current) => ({ ...current, reducer: value }))}
                    >
                      {value.toUpperCase()}
                    </button>
                  ))}
                </div>
              </div>
              <div className="parameter-grid">
                <label className="field">
                  <span>Число измерений</span>
                  <input
                    type="number"
                    min="2"
                    max="20"
                    value={request.reducer_components}
                    onChange={(event) =>
                      setRequest((current) => ({
                        ...current,
                        reducer_components: Number(event.target.value),
                      }))
                    }
                  />
                </label>
                {request.reducer === 'umap' && (
                  <>
                    <label className="field">
                      <span>Соседи UMAP</span>
                      <input
                        type="number"
                        min="2"
                        max="200"
                        value={request.umap_neighbors}
                        onChange={(event) =>
                          setRequest((current) => ({
                            ...current,
                            umap_neighbors: Number(event.target.value),
                          }))
                        }
                      />
                    </label>
                    <label className="field">
                      <span>Минимальное расстояние UMAP</span>
                      <input
                        type="number"
                        min="0"
                        max="1"
                        step="0.01"
                        value={request.umap_min_dist}
                        onChange={(event) =>
                          setRequest((current) => ({
                            ...current,
                            umap_min_dist: Number(event.target.value),
                          }))
                        }
                      />
                    </label>
                    <label className="field">
                      <span>Метрика UMAP</span>
                      <select
                        value={request.umap_metric}
                        onChange={(event) =>
                          setRequest((current) => ({
                            ...current,
                            umap_metric: event.target.value as ClusteringRequest['umap_metric'],
                          }))
                        }
                      >
                        <option value="cosine">Косинусная</option>
                        <option value="euclidean">Евклидова</option>
                        <option value="manhattan">Манхэттенская</option>
                      </select>
                    </label>
                  </>
                )}
                <label className="field">
                  <span>Начальное значение генератора</span>
                  <input
                    type="number"
                    value={request.random_state}
                    onChange={(event) =>
                      setRequest((current) => ({ ...current, random_state: Number(event.target.value) }))
                    }
                  />
                </label>
              </div>
              <div className="parameter-grid">
                <label className="field">
                  <span>Масштабирование</span>
                  <select
                    value={request.scaler}
                    onChange={(event) =>
                      setRequest((current) => ({
                        ...current,
                        scaler: event.target.value as ClusteringRequest['scaler'],
                      }))
                    }
                  >
                    <option value="robust">Устойчивое</option>
                    <option value="standard">Стандартное</option>
                  </select>
                </label>
                <label className="checkbox-field">
                  <input
                    type="checkbox"
                    checked={request.winsorize}
                    onChange={(event) =>
                      setRequest((current) => ({
                        ...current,
                        winsorize: event.target.checked,
                      }))
                    }
                  />
                  Винзоризация выбросов
                </label>
                <label className="checkbox-field">
                  <input
                    type="checkbox"
                    checked={request.log_transform}
                    onChange={(event) =>
                      setRequest((current) => ({
                        ...current,
                        log_transform: event.target.checked,
                      }))
                    }
                  />
                  Знаковое логарифмирование
                </label>
              </div>
              <div className="segmented-field">
                <span>Алгоритм</span>
                <div>
                  {(['hdbscan', 'kmeans'] as const).map((value) => (
                    <button
                      className={request.algorithm === value ? 'active' : ''}
                      type="button"
                      key={value}
                      onClick={() => setRequest((current) => ({ ...current, algorithm: value }))}
                    >
                      {value === 'hdbscan' ? 'HDBSCAN' : 'KMeans'}
                    </button>
                  ))}
                </div>
              </div>
              {request.algorithm === 'hdbscan' ? (
                <div className="parameter-grid">
                  <label className="field">
                    <span>Минимальный размер кластера</span>
                    <input
                      type="number"
                      min="2"
                      max="1000"
                      value={request.min_cluster_size}
                      onChange={(event) =>
                        setRequest((current) => ({ ...current, min_cluster_size: Number(event.target.value) }))
                      }
                    />
                  </label>
                  <label className="field">
                    <span>Минимум образцов</span>
                    <input
                      type="number"
                      min="1"
                      max="1000"
                      value={request.min_samples ?? ''}
                      onChange={(event) =>
                        setRequest((current) => ({
                          ...current,
                          min_samples: event.target.value ? Number(event.target.value) : null,
                        }))
                      }
                    />
                  </label>
                  <label className="field">
                    <span>Метод выбора</span>
                    <select
                      value={request.cluster_selection_method}
                      onChange={(event) =>
                        setRequest((current) => ({
                          ...current,
                          cluster_selection_method:
                            event.target.value as ClusteringRequest['cluster_selection_method'],
                        }))
                      }
                    >
                      <option value="eom">Избыток массы</option>
                      <option value="leaf">Лист</option>
                    </select>
                  </label>
                </div>
              ) : (
                <label className="field">
                  <span>Число кластеров</span>
                  <input
                    type="number"
                    min="2"
                    max="50"
                    value={request.n_clusters}
                    onChange={(event) =>
                      setRequest((current) => ({ ...current, n_clusters: Number(event.target.value) }))
                    }
                  />
                </label>
              )}
              {submitted && errors.length > 0 && (
                <ul className="validation-list" role="alert">
                  {errors.map((error) => <li key={error}>{error}</li>)}
                </ul>
              )}
              <button
                className="button primary wide"
                type="button"
                disabled={startMutation.isPending}
                onClick={() => {
                  setSubmitted(true)
                  if (!errors.length) startMutation.mutate()
                }}
              >
                <Play size={17} /> {startMutation.isPending ? 'Запуск…' : 'Запустить кластеризацию'}
              </button>
              {startMutation.error && <p className="inline-error">{startMutation.error.message}</p>}
            </>
          )}
        </aside>

        <section className="panel visualization-panel">
          {jobs.isLoading && <LoadingState label="Загрузка запусков кластеризации" />}
          {jobs.error && <ErrorState error={jobs.error} onRetry={() => void jobs.refetch()} />}
          {!selectedJob && !jobs.isLoading && (
            <EmptyState
              title="Запуск не выбран"
              message="Настройте и запустите кластеризацию, чтобы увидеть интерактивную проекцию."
            />
          )}
          {selectedJob && selectedJob.status !== 'completed' && (
            <div className="current-cluster-job">
              <JobCard
                job={selectedJob}
                onAction={
                  selectedJob.status === 'running' || selectedJob.status === 'queued'
                    ? () => stopMutation.mutate(selectedJob.id)
                    : undefined
                }
              />
            </div>
          )}
          {selectedJob?.status === 'completed' && selectedJob.points?.length ? (
            <ClusterPlot job={selectedJob} />
          ) : null}
          {selectedJob?.status === 'completed' && !selectedJob.points?.length && (
            <EmptyState title="Нет точек для отображения" message="Запуск завершён без данных о распределении." />
          )}
        </section>
      </div>

      <section className="panel clustering-history">
        <div className="section-heading">
          <div><h2>История запусков</h2><p>Выберите запуск для просмотра результата.</p></div>
        </div>
        <div className="job-list horizontal">
          {jobs.data?.map((job) => (
            <JobCard
              key={job.id}
              job={job}
              selected={job.id === selectedJobId}
              onSelect={() => setSelectedJobId(job.id)}
            />
          ))}
        </div>
      </section>
    </div>
  )
}
