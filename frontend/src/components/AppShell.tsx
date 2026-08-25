import { Database, ScrollText, Settings, WalletCards } from 'lucide-react'
import type { ReactNode } from 'react'

export type PageId = 'wallets' | 'features' | 'clustering' | 'logs' | 'settings'

const pages = [
  { id: 'wallets' as const, label: 'Кошельки', icon: WalletCards },
  { id: 'features' as const, label: 'Признаки', icon: Database },
  { id: 'logs' as const, label: 'Журнал', icon: ScrollText },
  { id: 'settings' as const, label: 'Настройки', icon: Settings },
]

interface AppShellProps {
  page: PageId
  onPageChange: (page: PageId) => void
  children: ReactNode
}

export function AppShell({ page, onPageChange, children }: AppShellProps) {
  return (
    <div className="app-shell">
      <aside className="sidebar" aria-label="Основная навигация">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">W</span>
          <span>
            <strong>Wallet Lens</strong>
            <small>Рабочая область анализа</small>
          </span>
        </div>
        <nav>
          {pages.map(({ id, label, icon: Icon }) => (
            <button
              className={page === id ? 'nav-item active' : 'nav-item'}
              type="button"
              key={id}
              aria-current={page === id ? 'page' : undefined}
              onClick={() => onPageChange(id)}
            >
              <Icon size={19} aria-hidden="true" />
              <span>{label}</span>
            </button>
          ))}
        </nav>
        <div className="sidebar-footer">
          <span className="status-dot" />
          API подключён
        </div>
      </aside>
      <main className="main-content">{children}</main>
    </div>
  )
}
