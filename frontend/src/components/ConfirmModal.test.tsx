import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ConfirmModal } from './ConfirmModal'


describe('ConfirmModal', () => {
  it('renders a styled confirmation and invokes the action', async () => {
    const onConfirm = vi.fn()
    render(
      <ConfirmModal
        open
        title="Удаление пакета"
        message="Пакет и его логи будут удалены."
        onConfirm={onConfirm}
        onClose={vi.fn()}
      />,
    )

    expect(screen.getByRole('dialog', { name: 'Удаление пакета' })).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Удалить' }))
    expect(onConfirm).toHaveBeenCalledOnce()
  })
})
