import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query'
import { Link, Outlet, createRootRoute, useRouterState } from '@tanstack/react-router'
import { apiGet, type Health } from '../lib/api'
import { ErrorState } from '../components/State'
import { Stamp } from '../components/Stamp'

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
  const session = health.data?.data.last_session

  return (
    <div className="app">
      <header className="appbar">
        <span className="wordmark">Opções B3</span>
        <Stamp session={session} loading={health.isLoading} />
      </header>
      <main>
        {health.isError && <ErrorState error={health.error} title="Saúde do lake indisponível" />}
        <Outlet />
      </main>
      <nav className="nav" aria-label="Navegação principal">
        {NAV.map((item) => (
          <Link
            key={item.to}
            to={item.to}
            className={pathname === item.to ? 'active' : undefined}
            aria-current={pathname === item.to ? 'page' : undefined}
          >
            {item.label}
          </Link>
        ))}
      </nav>
    </div>
  )
}
