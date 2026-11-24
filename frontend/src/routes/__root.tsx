import { useEffect, useState } from 'react'
import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query'
import { Link, Outlet, createRootRoute, useRouterState } from '@tanstack/react-router'
import { apiGet, type Health } from '../lib/api'
import { ErrorState } from '../components/State'
import { Stamp } from '../components/Stamp'
import {
  OfflineBanner,
  persistHealth,
  readPersistedHealth,
  useConnectivity,
  type LastHealth,
} from '../components/OfflineBanner'

// Leitura de sessão: 60 s. Glossário/tickers passam staleTime de 1 h nas rotas.
const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 60_000, retry: 1, refetchOnWindowFocus: false },
  },
})

export const Route = createRootRoute({
  component: RootLayout,
})

const NAV = [
  { to: '/', label: 'Hoje' },
  { to: '/carteira', label: 'Carteira' },
  { to: '/diario', label: 'Diário' },
  { to: '/aprender', label: 'Aprender' },
] as const

/** O Provider fica no __root (contrato), mas o consumidor precisa ser FILHO:
 *  um hook chamado no mesmo componente que retorna o Provider não vê o contexto. */
function RootLayout() {
  return (
    <QueryClientProvider client={queryClient}>
      <AppShell />
    </QueryClientProvider>
  )
}

function AppShell() {
  const pathname = useRouterState({ select: (state) => state.location.pathname })
  const health = useQuery({
    queryKey: ['health'],
    queryFn: ({ signal }) => apiGet<Health>('/api/health', signal),
  })
  const offline = useConnectivity()
  const [last] = useState<LastHealth | null>(() => readPersistedHealth())

  // Cada sucesso grava a sessão para o modo offline (persiste entre reloads).
  useEffect(() => {
    if (health.data) persistHealth(health.data.data)
  }, [health.data])

  const onlineSession = health.data?.data.last_session ?? null
  const ageSessions =
    typeof health.data?.data.age_sessions === 'number' ? health.data.data.age_sessions : null
  const knownSession = onlineSession ?? last?.session ?? null
  const shownSession = offline ? knownSession : onlineSession

  return (
    <div className="app">
      <header className="appbar">
        <span className="wordmark lg:text-block-title">Opções B3</span>
        <Stamp
          session={shownSession}
          loading={health.isLoading && !offline}
          ageSessions={ageSessions}
          offline={offline}
        />
      </header>
      <main>
        <OfflineBanner offline={offline} session={knownSession} />
        {health.isError && !offline && (
          <ErrorState error={health.error} title="Saúde do lake indisponível" />
        )}
        <Outlet />
      </main>
      <nav className="nav" aria-label="Navegação principal">
        {NAV.map((item) => (
          <Link
            key={item.to}
            to={item.to}
            className={`${pathname === item.to ? 'active' : ''} lg:text-label`.trim()}
            aria-current={pathname === item.to ? 'page' : undefined}
          >
            {item.label}
          </Link>
        ))}
      </nav>
    </div>
  )
}
