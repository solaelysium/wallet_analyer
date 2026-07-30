import { render } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { FeatureTable } from './FeatureTable'


describe('FeatureTable', () => {
  it('keeps the header inside the horizontal scroll container', () => {
    const { container } = render(
      <FeatureTable
        rows={[{ walletId: 1, address: '0x0000000000000000000000000000000000000001', score: 1 }]}
        schema={[
          { id: 'address', label: 'Адрес', type: 'string', source: 'base' },
          { id: 'score', label: 'Оценка', type: 'number', source: 'feature' },
        ]}
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

    const scrollContainer = container.querySelector('.table-scroll')
    expect(scrollContainer).not.toBeNull()
    expect(scrollContainer?.querySelector('.table-header')).not.toBeNull()
    expect(container.querySelector('.data-table > .table-header')).toBeNull()
  })
})
