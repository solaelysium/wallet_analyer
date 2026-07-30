import { lazy, Suspense, useState } from 'react'
import { AppShell, type PageId } from './components/AppShell'
import { LoadingState } from './components/States'

const WalletsPage = lazy(() =>
  import('./features/wallets/WalletsPage').then((module) => ({ default: module.WalletsPage })),
)
const FeaturesPage = lazy(() =>
  import('./features/features/FeaturesPage').then((module) => ({ default: module.FeaturesPage })),
)
const ClusteringPage = lazy(() =>
  import('./features/clustering/ClusteringPage').then((module) => ({ default: module.ClusteringPage })),
)
const LogsPage = lazy(() =>
  import('./features/logs/LogsPage').then((module) => ({ default: module.LogsPage })),
)
const SettingsPage = lazy(() =>
  import('./features/settings/SettingsPage').then((module) => ({ default: module.SettingsPage })),
)

function App() {
  const [page, setPage] = useState<PageId>('wallets')
  return (
    <AppShell page={page} onPageChange={setPage}>
      <Suspense fallback={<LoadingState label="Загрузка рабочей области" />}>
        {page === 'wallets' && <WalletsPage />}
        {page === 'features' && <FeaturesPage />}
        {page === 'clustering' && <ClusteringPage />}
        {page === 'logs' && <LogsPage />}
        {page === 'settings' && <SettingsPage />}
      </Suspense>
    </AppShell>
  )
}

export default App
