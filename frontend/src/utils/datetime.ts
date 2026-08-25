/** Display timezone: Moscow (UTC+3 year-round). */
export const APP_TIME_ZONE = 'Europe/Moscow'

const dateTimeOptions: Intl.DateTimeFormatOptions = {
  timeZone: APP_TIME_ZONE,
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
}

const dateOptions: Intl.DateTimeFormatOptions = {
  timeZone: APP_TIME_ZONE,
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
}

const timeOptions: Intl.DateTimeFormatOptions = {
  timeZone: APP_TIME_ZONE,
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
}

/** API timestamps are UTC; naive strings without offset are treated as UTC. */
export function parseApiDate(value: string | Date): Date {
  if (value instanceof Date) return value
  const trimmed = value.trim()
  if (!trimmed) return new Date(Number.NaN)
  if (/[zZ]$|[+-]\d{2}:?\d{2}$/.test(trimmed)) return new Date(trimmed)
  return new Date(`${trimmed}Z`)
}

export function formatDateTime(value: string | Date | null | undefined) {
  if (value == null || value === '') return '—'
  const date = parseApiDate(value)
  if (Number.isNaN(date.getTime())) return String(value)
  return new Intl.DateTimeFormat('ru-RU', dateTimeOptions).format(date)
}

export function formatDate(value: string | Date | null | undefined) {
  if (value == null || value === '') return '—'
  const date = parseApiDate(value)
  if (Number.isNaN(date.getTime())) return String(value)
  return new Intl.DateTimeFormat('ru-RU', dateOptions).format(date)
}

export function formatTime(value: string | Date | null | undefined) {
  if (value == null || value === '') return '—'
  const date = parseApiDate(value)
  if (Number.isNaN(date.getTime())) return String(value)
  return new Intl.DateTimeFormat('ru-RU', timeOptions).format(date)
}
