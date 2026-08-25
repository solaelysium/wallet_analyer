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
      lastAnalysisStatus: 'completed',
      lastAnalysisError: null,
    },
    {
      address: newAddress,
      checksumAddress: newAddress,
      source: 'Ручной ввод',
      row: 2,
      sourceIndex: null,
      alreadyAnalyzed: false,
      lastAnalyzedAt: null,
      lastAnalysisStatus: null,
      lastAnalysisError: null,
    },
  ],
  issues: [],
}

describe('PreviewModal', () => {
  it('shows prior analysis status and allows selecting individual wallets', async () => {
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
    expect(screen.getByText(/Проверен .* · успешно/)).toBeInTheDocument()
    expect(screen.getByText('Ранее не проверялся')).toBeInTheDocument()

    await user.click(screen.getAllByRole('checkbox')[0])
    expect(onToggleAddress).toHaveBeenCalledWith(existingAddress)

    await user.click(screen.getByRole('button', { name: 'Только новые' }))
    expect(onSelectOnlyNew).toHaveBeenCalledOnce()
  })

  it('shows skipped and failed prior statuses with error details', () => {
    const mixedPreview: WalletPreview = {
      ...preview,
      analyzedCount: 2,
      entries: [
        {
          ...preview.entries[0],
          lastAnalysisStatus: 'skipped',
          lastAnalysisError: 'Более 25 000 транзакций',
        },
        {
          ...preview.entries[1],
          alreadyAnalyzed: true,
          lastAnalyzedAt: '2026-07-30T13:00:00Z',
          lastAnalysisStatus: 'failed',
          lastAnalysisError: 'timeout',
        },
      ],
    }

    render(
      <PreviewModal
        preview={mixedPreview}
        open
        batchName="Wallet batch"
        chain="ethereum"
        onBatchNameChange={vi.fn()}
        onChainChange={vi.fn()}
        onClose={vi.fn()}
        onConfirm={vi.fn()}
        excludedAddresses={new Set()}
        onToggleAddress={vi.fn()}
        onSelectAll={vi.fn()}
        onSelectOnlyNew={vi.fn()}
        confirming={false}
      />,
    )

    expect(screen.getByText(/пропущено: Более 25 000 транзакций/)).toBeInTheDocument()
    expect(screen.getByText(/с ошибкой: timeout/)).toBeInTheDocument()
  })
})
