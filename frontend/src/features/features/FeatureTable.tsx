import {
  flexRender,
  getCoreRowModel,
  useReactTable,
  type ColumnDef,
  type ColumnOrderState,
  type VisibilityState,
} from '@tanstack/react-table'
import { useVirtualizer } from '@tanstack/react-virtual'
import { ArrowDown, ArrowUp, ChevronLeft, ChevronRight, Copy, GripVertical, Settings2, Trash2 } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import type { FeatureColumn, FeatureRow } from '../../api/types'
import { ConfirmModal } from '../../components/ConfirmModal'
import { formatFeatureValue } from './formatters'

interface FeatureTableProps {
  rows: FeatureRow[]
  schema: FeatureColumn[]
  total: number
  page: number
  pageSize: number
  sortBy: string
  sortDirection: 'asc' | 'desc'
  onPageChange: (page: number) => void
  onSort: (id: string) => void
  onDeleteWallets: (walletIds: number[]) => Promise<void>
}

export function FeatureTable({
  rows,
  schema,
  total,
  page,
  pageSize,
  sortBy,
  sortDirection,
  onPageChange,
  onSort,
  onDeleteWallets,
}: FeatureTableProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [visibility, setVisibility] = useState<VisibilityState>({})
  const [columnOrder, setColumnOrder] = useState<ColumnOrderState>([])
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [anchor, setAnchor] = useState<{ row: number; column: number } | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [deleteConfirmationOpen, setDeleteConfirmationOpen] = useState(false)

  const columns = useMemo<ColumnDef<FeatureRow>[]>(
    () =>
      schema.map((column) => ({
        id: column.id,
        accessorFn: (row) => row[column.id],
        header: column.label,
        cell: (info) => formatFeatureValue(
          info.getValue() as FeatureRow[string],
          column.type,
          column.id,
        ),
        enableSorting: column.source !== 'quality',
      })),
    [schema],
  )
  const table = useReactTable({
    data: rows,
    columns,
    state: { columnVisibility: visibility, columnOrder },
    onColumnVisibilityChange: setVisibility,
    onColumnOrderChange: setColumnOrder,
    getCoreRowModel: getCoreRowModel(),
    manualSorting: true,
    manualPagination: true,
  })
  const visibleColumns = table.getVisibleLeafColumns()
  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => containerRef.current,
    estimateSize: () => 43,
    overscan: 8,
  })
  const gridTemplate = visibleColumns
    .map((column) => (column.id === 'address' ? 'minmax(250px, 1.8fr)' : 'minmax(140px, 1fr)'))
    .join(' ')
  const selectedWalletIds = Array.from(new Set(
    Array.from(selected)
      .map((key) => Number(key.split(':')[0]))
      .map((rowIndex) => rows[rowIndex]?.walletId)
      .filter((walletId): walletId is number => walletId !== undefined),
  ))

  useEffect(() => {
    setSelected(new Set())
    setAnchor(null)
  }, [page, rows])

  function selectCell(rowIndex: number, columnIndex: number, extend: boolean) {
    if (extend && anchor) {
      const next = new Set<string>()
      for (let row = Math.min(anchor.row, rowIndex); row <= Math.max(anchor.row, rowIndex); row += 1) {
        for (
          let column = Math.min(anchor.column, columnIndex);
          column <= Math.max(anchor.column, columnIndex);
          column += 1
        ) {
          next.add(`${row}:${column}`)
        }
      }
      setSelected(next)
      return
    }
    setAnchor({ row: rowIndex, column: columnIndex })
    setSelected(new Set([`${rowIndex}:${columnIndex}`]))
  }

  async function copySelection() {
    const values = Array.from(selected)
      .sort()
      .map((key) => {
        const [rowIndex, columnIndex] = key.split(':').map(Number)
        const column = visibleColumns[columnIndex]
        return column ? String(rows[rowIndex]?.[column.id] ?? '') : ''
      })
    await navigator.clipboard.writeText(values.join('\t'))
  }

  async function copyColumn(columnId: string) {
    await navigator.clipboard.writeText(rows.map((row) => String(row[columnId] ?? '')).join('\n'))
  }

  function moveColumn(id: string, direction: -1 | 1) {
    if (id === 'address') return
    const ids = visibleColumns.map((column) => column.id)
    const from = ids.indexOf(id)
    const to = from + direction
    if (to < 1 || to >= ids.length) return
    ;[ids[from], ids[to]] = [ids[to], ids[from]]
    setColumnOrder(ids)
  }

  async function deleteSelectedWallets() {
    if (!selectedWalletIds.length) return
    setDeleting(true)
    try {
      await onDeleteWallets(selectedWalletIds)
      setSelected(new Set())
      setAnchor(null)
      setDeleteConfirmationOpen(false)
    } catch {
      // The parent mutation renders the API error.
    } finally {
      setDeleting(false)
    }
  }

  return (
    <>
      <div className="table-toolbar">
        <button className="button secondary small" type="button" disabled={!selected.size} onClick={() => void copySelection()}>
          <Copy size={15} /> Копировать выбранное
        </button>
        <button
          className="button secondary small danger"
          type="button"
          disabled={!selectedWalletIds.length || deleting}
          onClick={() => setDeleteConfirmationOpen(true)}
        >
          <Trash2 size={15} /> {deleting ? 'Удаление…' : `Удалить адреса (${selectedWalletIds.length})`}
        </button>
        <details className="column-menu">
          <summary className="button secondary small"><Settings2 size={15} /> Столбцы</summary>
          <div className="column-menu-popover">
            {table.getAllLeafColumns().map((column) => (
              <div key={column.id}>
                <label>
                  <input
                    type="checkbox"
                    checked={column.getIsVisible()}
                    disabled={column.id === 'address'}
                    onChange={column.getToggleVisibilityHandler()}
                  />
                  {schema.find((item) => item.id === column.id)?.label ?? column.id}
                </label>
                <span>
                  <button type="button" disabled={column.id === 'address'} aria-label={`Переместить ${column.id} влево`} onClick={() => moveColumn(column.id, -1)}>
                    <ChevronLeft size={14} />
                  </button>
                  <button type="button" disabled={column.id === 'address'} aria-label={`Переместить ${column.id} вправо`} onClick={() => moveColumn(column.id, 1)}>
                    <ChevronRight size={14} />
                  </button>
                </span>
              </div>
            ))}
          </div>
        </details>
        <span className="selection-hint">Выберите ячейку, затем нажмите Shift для выбора диапазона.</span>
      </div>
      <div className="data-table" role="table" aria-rowcount={total}>
        <div className="table-scroll" ref={containerRef}>
          <div className="table-header table-grid" role="row" style={{ gridTemplateColumns: gridTemplate }}>
            {table.getHeaderGroups()[0]?.headers.map((header) => (
              <div className={header.column.id === 'address' ? 'sticky-column' : ''} role="columnheader" key={header.id}>
                <GripVertical size={13} aria-hidden="true" />
                <button
                  type="button"
                  disabled={!header.column.getCanSort()}
                  title={header.column.getCanSort() ? undefined : 'Сортировка полей качества недоступна'}
                  onClick={() => {
                    if (header.column.getCanSort()) onSort(header.column.id)
                  }}
                >
                  {flexRender(header.column.columnDef.header, header.getContext())}
                  {sortBy === header.column.id && (sortDirection === 'asc' ? <ArrowUp size={13} /> : <ArrowDown size={13} />)}
                </button>
                <button
                  className="copy-column"
                  type="button"
                  aria-label={`Копировать столбец ${header.column.id}`}
                  onClick={() => void copyColumn(header.column.id)}
                >
                  <Copy size={13} />
                </button>
              </div>
            ))}
          </div>
          <div style={{ height: `${virtualizer.getTotalSize()}px`, position: 'relative', minWidth: '900px' }}>
            {virtualizer.getVirtualItems().map((virtualRow) => {
              const row = table.getRowModel().rows[virtualRow.index]
              return (
                <div
                  className="table-row table-grid"
                  role="row"
                  key={row.id}
                  style={{
                    gridTemplateColumns: gridTemplate,
                    transform: `translateY(${virtualRow.start}px)`,
                  }}
                >
                  {row.getVisibleCells().map((cell, columnIndex) => {
                    const key = `${virtualRow.index}:${columnIndex}`
                    return (
                      <button
                        className={`table-cell${cell.column.id === 'address' ? ' sticky-column address-cell' : ''}${selected.has(key) ? ' selected' : ''}`}
                        role="cell"
                        type="button"
                        key={cell.id}
                        title={String(cell.getValue() ?? '')}
                        onClick={(event) => selectCell(virtualRow.index, columnIndex, event.shiftKey)}
                        onDoubleClick={() => void navigator.clipboard.writeText(String(cell.getValue() ?? ''))}
                      >
                        {flexRender(cell.column.columnDef.cell, cell.getContext())}
                      </button>
                    )
                  })}
                </div>
              )
            })}
          </div>
        </div>
      </div>
      <footer className="pagination">
        <span>
          {total ? page * pageSize + 1 : 0}–{Math.min((page + 1) * pageSize, total)} из {total.toLocaleString('ru-RU')}
        </span>
        <div>
          <button className="icon-button" type="button" disabled={page === 0} onClick={() => onPageChange(page - 1)} aria-label="Предыдущая страница">
            <ChevronLeft size={18} />
          </button>
          <span>Страница {page + 1} из {Math.max(1, Math.ceil(total / pageSize))}</span>
          <button
            className="icon-button"
            type="button"
            disabled={(page + 1) * pageSize >= total}
            onClick={() => onPageChange(page + 1)}
            aria-label="Следующая страница"
          >
            <ChevronRight size={18} />
          </button>
        </div>
      </footer>
      <ConfirmModal
        open={deleteConfirmationOpen}
        title="Удаление кошельков"
        message={
          selectedWalletIds.length === 1
            ? 'Кошелёк и все связанные транзакции, признаки и результаты кластеризации будут удалены.'
            : `${selectedWalletIds.length} кошельков и все связанные данные будут удалены.`
        }
        confirming={deleting}
        onConfirm={() => void deleteSelectedWallets()}
        onClose={() => setDeleteConfirmationOpen(false)}
      />
    </>
  )
}
