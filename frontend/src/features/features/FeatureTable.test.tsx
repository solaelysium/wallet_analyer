import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { FeatureTable } from './FeatureTable'

vi.mock('@tanstack/react-virtual', () => ({
  useVirtualizer: ({ count }: { count: number }) => ({
    getTotalSize: () => count * 43,
    getVirtualItems: () =>
      Array.from({ length: count }, (_, index) => ({
        index,
        key: index,
        start: index * 43,
        size: 43,
        end: (index + 1) * 43,
      })),
  }),
}))

afterEach(() => {
  cleanup()
})

const address = '0x0000000000000000000000000000000000000001'

function manySchema(count: number) {
  return [
    { id: 'address', label: 'Адрес', type: 'string' as const, source: 'base' as const },
    ...Array.from({ length: count }, (_, index) => ({
      id: `f${index}`,
      label: `F${index}`,
      type: 'number' as const,
      source: 'feature' as const,
    })),
  ]
}

function renderTable(schema = manySchema(1)) {
  const row = Object.fromEntries([
    ['walletId', 1],
    ['address', address],
    ...schema.filter((column) => column.id !== 'address').map((column) => [column.id, 1]),
  ])
  return render(
    <FeatureTable
      rows={[row as { walletId: number; address: string }]}
      schema={schema}
      total={1}
      page={0}
      pageSize={100}
      sortBy="address"
      sortDirection="asc"
      onPageChange={vi.fn()}
      onSort={vi.fn()}
      onDeleteWallets={vi.fn()}
    />,
  )
}

describe('FeatureTable', () => {
  it('keeps the header inside the horizontal scroll container', () => {
    const { container } = renderTable()

    const scrollContainer = container.querySelector('.table-scroll')
    expect(scrollContainer).not.toBeNull()
    expect(scrollContainer?.querySelector('.table-header')).not.toBeNull()
    expect(container.querySelector('.data-table > .table-header')).toBeNull()
  })

  it('pins the address column without transform on virtual rows', () => {
    const featureCount = 20
    const { container } = renderTable(manySchema(featureCount))
    const row = container.querySelector('.table-row') as HTMLElement | null
    const expectedWidth = `${250 + featureCount * 140}px`
    expect(row).not.toBeNull()
    expect(row?.style.transform).toBe('')
    expect(row?.style.top).toBe('0px')
    expect(row?.style.width).toBe(expectedWidth)
    expect(container.querySelectorAll('.sticky-column').length).toBeGreaterThanOrEqual(2)
  })

  it('copies the address and shows a toast on address cell click', async () => {
    const user = userEvent.setup()
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    })

    const { container } = renderTable()
    const addressCell = container.querySelector('.address-cell')
    expect(addressCell).not.toBeNull()
    await user.click(addressCell!)

    expect(writeText).toHaveBeenCalledWith(address)
    expect(screen.getByRole('status')).toHaveTextContent('Адрес скопирован')
  })

  it('copies selected cells with Ctrl+C regardless of keyboard layout', async () => {
    const user = userEvent.setup()
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    })

    const { container } = renderTable()
    const scoreCell = container.querySelector('.table-cell:not(.address-cell)') as HTMLElement | null
    expect(scoreCell).not.toBeNull()
    await user.click(scoreCell!)

    writeText.mockClear()
    // Russian layout still reports code=KeyC for the physical C key.
    window.dispatchEvent(new KeyboardEvent('keydown', {
      bubbles: true,
      cancelable: true,
      ctrlKey: true,
      code: 'KeyC',
      key: 'с',
    }))

    await vi.waitFor(() => {
      expect(writeText).toHaveBeenCalledWith('1')
    })
    expect(writeText).toHaveBeenCalledTimes(1)
  })
})
