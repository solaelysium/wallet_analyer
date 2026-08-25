import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Download, Filter, Search } from 'lucide-react'
import { useState } from 'react'
import { deleteWallet, exportFeatures, getFeatures } from '../../api/client'
import type { FeatureQuery } from '../../api/types'
import { EmptyState, ErrorState, LoadingState } from '../../components/States'
import { FeatureTable } from './FeatureTable'
import { downloadBlob } from './formatters'

const initialQuery: FeatureQuery = {
  page: 0,
  pageSize: 100,
  search: '',
  sortBy: 'address',
  sortDirection: 'asc',
  filters: {},
}

export function FeaturesPage() {
  const queryClient = useQueryClient()
  const [query, setQuery] = useState(initialQuery)
  const [filterColumn, setFilterColumn] = useState('')
  const [filterMin, setFilterMin] = useState('')
  const [filterMax, setFilterMax] = useState('')
  const [exporting, setExporting] = useState('')
  const features = useQuery({
    queryKey: ['features', query],
    queryFn: () => getFeatures(query),
    placeholderData: (previous) => previous,
  })
  const deleteMutation = useMutation({
    mutationFn: async (walletIds: number[]) => {
      for (const walletId of walletIds) await deleteWallet(walletId)
    },
    onSuccess: (_, walletIds) => {
      if (query.page > 0 && walletIds.length >= (features.data?.rows.length ?? 0)) {
        setQuery((current) => ({ ...current, page: Math.max(0, current.page - 1) }))
      }
      void queryClient.invalidateQueries({ queryKey: ['features'] })
      void queryClient.invalidateQueries({ queryKey: ['wallet-jobs'] })
      void queryClient.invalidateQueries({ queryKey: ['current-feature-dataset'] })
      void queryClient.invalidateQueries({ queryKey: ['clustering-jobs'] })
    },
  })

  function applyFilter() {
    if (!filterColumn || (!filterMin && !filterMax)) return
    const limits = {
      ...(filterMin ? { min: Number(filterMin) } : {}),
      ...(filterMax ? { max: Number(filterMax) } : {}),
    }
    setQuery((current) => ({
      ...current,
      page: 0,
      filters: { ...current.filters, [filterColumn]: limits },
    }))
  }

  function removeFilter(id: string) {
    setQuery((current) => {
      const filters = { ...current.filters }
      delete filters[id]
      return { ...current, page: 0, filters }
    })
  }

  async function runExport(format: 'csv' | 'xlsx', scope: 'filtered' | 'all') {
    const key = `${scope}-${format}`
    setExporting(key)
    try {
      const blob = await exportFeatures(query, format, scope)
      downloadBlob(blob, `wallet-features-${scope}.${format}`)
    } finally {
      setExporting('')
    }
  }

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <span className="eyebrow">Просмотр набора данных</span>
          <h1>Признаки</h1>
          <p>Просматривайте, выбирайте, копируйте и экспортируйте рассчитанные признаки кошельков.</p>
        </div>
        <div className="export-actions">
          <details>
            <summary className="button secondary"><Download size={17} /> Экспорт по фильтрам</summary>
            <div className="menu-popover">
              <button type="button" onClick={() => void runExport('csv', 'filtered')}>CSV</button>
              <button type="button" onClick={() => void runExport('xlsx', 'filtered')}>XLSX</button>
            </div>
          </details>
          <details>
            <summary className="button primary"><Download size={17} /> Экспорт всех данных</summary>
            <div className="menu-popover">
              <button type="button" onClick={() => void runExport('csv', 'all')}>CSV</button>
              <button type="button" onClick={() => void runExport('xlsx', 'all')}>XLSX</button>
            </div>
          </details>
          {exporting && <span className="assistive-status" role="status">Подготовка экспорта {exporting}</span>}
        </div>
      </header>

      <section className="panel feature-panel">
        <div className="feature-controls">
          <label className="search-field">
            <Search size={15} />
            <span className="visually-hidden">Поиск адресов</span>
            <input
              type="search"
              placeholder="Поиск по адресу кошелька"
              value={query.search}
              onChange={(event) => setQuery((current) => ({ ...current, page: 0, search: event.target.value }))}
            />
          </label>
          <div className="filter-builder">
            <Filter size={15} />
            <select value={filterColumn} onChange={(event) => setFilterColumn(event.target.value)} aria-label="Столбец фильтра">
              <option value="">Столбец</option>
              {features.data?.columns
                .filter((column) => column.source === 'feature' && ['number', 'currency', 'percent'].includes(column.type))
                .map((column) => <option key={column.id} value={column.id}>{column.label}</option>)}
            </select>
            <input
              type="number"
              value={filterMin}
              placeholder="Минимум"
              aria-label="Минимальное значение фильтра"
              onChange={(event) => setFilterMin(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') applyFilter()
              }}
            />
            <input
              type="number"
              value={filterMax}
              placeholder="Максимум"
              aria-label="Максимальное значение фильтра"
              onChange={(event) => setFilterMax(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') applyFilter()
              }}
            />
            <button
              className="button secondary small"
              type="button"
              disabled={!filterColumn || (!filterMin && !filterMax)}
              onClick={applyFilter}
            >
              Применить
            </button>
          </div>
        </div>
        {Object.entries(query.filters).length > 0 && (
          <div className="filter-chips" aria-label="Активные фильтры">
            {Object.entries(query.filters).map(([id, value]) => (
              <button type="button" key={id} onClick={() => removeFilter(id)}>
                {id}: {value.min ?? '−∞'} — {value.max ?? '∞'} <span aria-hidden="true">×</span>
              </button>
            ))}
          </div>
        )}
        {features.isLoading && <LoadingState label="Загрузка признаков" />}
        {features.error && <ErrorState error={features.error} onRetry={() => void features.refetch()} />}
        {deleteMutation.error && <p className="inline-error" role="alert">{deleteMutation.error.message}</p>}
        {features.data?.rows.length === 0 && (
          <EmptyState title="Данных нет" message="Измените фильтры или сначала обработайте пакет кошельков." />
        )}
        {features.data && features.data.rows.length > 0 && (
          <FeatureTable
            rows={features.data.rows}
            schema={features.data.columns}
            total={features.data.total}
            page={query.page}
            pageSize={query.pageSize}
            sortBy={query.sortBy}
            sortDirection={query.sortDirection}
            onPageChange={(page) => setQuery((current) => ({ ...current, page }))}
            onSort={(id) =>
              setQuery((current) => ({
                ...current,
                page: 0,
                sortBy: id,
                sortDirection: current.sortBy === id && current.sortDirection === 'asc' ? 'desc' : 'asc',
              }))
            }
            onDeleteWallets={(walletIds) => deleteMutation.mutateAsync(walletIds)}
          />
        )}
      </section>
    </div>
  )
}
