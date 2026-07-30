import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { WalletPreview } from '../../api/types'
import { PreviewModal } from './PreviewModal'

const existingAddress = '0x1111111111111111111111111111111111111111'
const newAddress = '0x2222222222222222222222222222222222222222'

const preview: WalletPreview = {
  token: 'preview-token',
  validCount: 2,
  duplicateCount: 0,
  invalidCount: 0,
  analyzedCount: 1,
  sourceCount: 1,
  entries: [
    {
      address: existingAddress,
      checksumAddress: existingAddress,
      source: 'Ручной ввод',
      row: 1,
      sourceIndex: null,
      alreadyAnalyzed: true,
      lastAnalyzedAt: '2026-07-30T12:30:00Z',
    },
    {
      address: newAddress,
      checksumAddress: newAddress,
      source: 'Ручной ввод',
      row: 2,
      sourceIndex: null,
      alreadyAnalyzed: false,
      lastAnalyzedAt: null,
    },
  ],
  issues: [],
}

describe('PreviewModal', () => {
  it('shows prior analysis and allows selecting individual wallets', async () => {
    const user = userEvent.setup()
    const onToggleAddress = vi.fn()
    const onSelectOnlyNew = vi.fn()

    render(
      <PreviewModal
        preview={preview}
        open
        batchName="Wallet batch"
        chain="ethereum"
        onBatchNameChange={vi.fn()}
        onChainChange={vi.fn()}
        onClose={vi.fn()}
        onConfirm={vi.fn()}
        excludedAddresses={new Set()}
        onToggleAddress={onToggleAddress}
        onSelectAll={vi.fn()}
        onSelectOnlyNew={onSelectOnlyNew}
        confirming={false}
      />,
    )

    expect(screen.getByText('Уже проверялись ранее')).toBeInTheDocument()
    expect(screen.getByText(/Проверен 30 июл. 2026 г./)).toBeInTheDocument()
    expect(screen.getByText('Ранее не проверялся')).toBeInTheDocument()

    await user.click(screen.getAllByRole('checkbox')[0])
    expect(onToggleAddress).toHaveBeenCalledWith(existingAddress)

    await user.click(screen.getByRole('button', { name: 'Только новые' }))
    expect(onSelectOnlyNew).toHaveBeenCalledOnce()
  })
})
