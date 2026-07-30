import * as XLSX from 'xlsx'

export interface LocalImportSource {
  id: string
  name: string
  kind: 'csv' | 'xlsx' | 'txt' | 'manual'
  file?: File
  raw: string
  addresses: string[]
  invalidCount: number
}

const ADDRESS_PATTERN = /^0x[a-fA-F0-9]{40}$/

export function isWalletAddress(value: string): boolean {
  return ADDRESS_PATTERN.test(value.trim())
}

export function parseAddressLines(raw: string): { addresses: string[]; invalidCount: number } {
  const values = raw
    .split(/\r?\n/)
    .map((value) => value.trim())
    .filter(Boolean)
  return {
    addresses: values.filter(isWalletAddress),
    invalidCount: values.filter((value) => !isWalletAddress(value)).length,
  }
}

function recordsToAddresses(records: Record<string, unknown>[], sourceName: string) {
  if (!records.length) return { addresses: [], invalidCount: 0 }
  const columns = Object.keys(records[0])
  const indexColumns = columns.filter((column) => column !== 'wallet_address')
  if (
    columns.length !== 2
    || !Object.hasOwn(records[0], 'wallet_address')
    || indexColumns.length !== 1
    || !['index', '__EMPTY'].includes(indexColumns[0])
  ) {
    throw new Error(`${sourceName}: нужны только столбцы index и wallet_address`)
  }
  const values = records.map((record) => String(record.wallet_address ?? '').trim())
  return {
    addresses: values.filter(isWalletAddress),
    invalidCount: values.filter((value) => !isWalletAddress(value)).length,
  }
}

export async function parseImportFile(file: File): Promise<LocalImportSource> {
  const extension = file.name.split('.').pop()?.toLowerCase()
  if (!extension || !['csv', 'xlsx', 'txt'].includes(extension)) {
    throw new Error(`${file.name}: неподдерживаемый тип файла`)
  }

  let parsed: { addresses: string[]; invalidCount: number }
  let raw = ''
  if (extension === 'txt') {
    raw = await file.text()
    parsed = parseAddressLines(raw)
  } else {
    const workbook =
      extension === 'csv'
        ? XLSX.read((raw = await file.text()), { type: 'string', raw: true })
        : XLSX.read(await file.arrayBuffer(), { type: 'array' })
    const sheet = workbook.Sheets[workbook.SheetNames[0]]
    const records = XLSX.utils.sheet_to_json<Record<string, unknown>>(sheet, { defval: '' })
    parsed = recordsToAddresses(records, file.name)
  }

  return {
    id: crypto.randomUUID(),
    name: file.name,
    kind: extension as 'csv' | 'xlsx' | 'txt',
    file,
    raw,
    ...parsed,
  }
}

export function createManualSource(raw: string): LocalImportSource | null {
  if (!raw.trim()) return null
  return {
    id: crypto.randomUUID(),
    name: 'Ручной ввод',
    kind: 'manual',
    raw,
    ...parseAddressLines(raw),
  }
}

export function getAggregateStats(sources: LocalImportSource[]) {
  const all = sources.flatMap((source) => source.addresses.map((address) => address.toLowerCase()))
  return {
    rows: sources.reduce((sum, source) => sum + source.addresses.length + source.invalidCount, 0),
    valid: all.length,
    unique: new Set(all).size,
    duplicates: all.length - new Set(all).size,
    invalid: sources.reduce((sum, source) => sum + source.invalidCount, 0),
  }
}
