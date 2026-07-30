import type { FeatureColumn } from '../../api/types'

export function formatFeatureValue(
  value: string | number | boolean | null,
  type: FeatureColumn['type'],
): string {
  if (value === null || value === '') return '—'
  if (type === 'boolean') return value ? 'Да' : 'Нет'
  if (type === 'date') {
    const date = new Date(String(value))
    return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString('ru-RU')
  }
  if (type === 'currency' && typeof value === 'number') {
    return new Intl.NumberFormat('ru-RU', { style: 'currency', currency: 'USD', maximumFractionDigits: 2 }).format(value)
  }
  if (type === 'percent' && typeof value === 'number') {
    return new Intl.NumberFormat('ru-RU', { style: 'percent', maximumFractionDigits: 2 }).format(value)
  }
  if (type === 'number' && typeof value === 'number') {
    return new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 6 }).format(value)
  }
  return String(value)
}

export function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.click()
  URL.revokeObjectURL(url)
}
