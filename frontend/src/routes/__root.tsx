import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query'
import { Link, Outlet, createRootRoute, useRouterState } from '@tanstack/react-router'
import { apiGet, type Health } from '../lib/api'
import { ErrorBox } from '../lib/ui'

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

function RootLayout() {
  const pathname = useRouterState({ select: (state) => state.location.pathname })
  const health = useQuery({
    queryKey: ['health'],
    queryFn: ({ signal }) => apiGet<Health>('/api/health', signal),
  })
  const session = health.data?.data.last_session

  return (
    <QueryClientProvider client={queryClient}>
      <div className="app">
        <header className="appbar">
          <h1>Opções B3</h1>
          <div className="stamp">
            {health.isLoading
              ? 'carregando sessão…'
              : session
                ? `sessão ${session} · D-1`
                : 'sessão indisponível'}
          </div>
        </header>
        <main>
          <ErrorBox error={health.error} />
          <Outlet />
        </main>
        <nav className="nav">
          {NAV.map((item) => (
            <Link
              key={item.to}
              to={item.to}
              className={pathname === item.to ? 'active' : undefined}
            >
              {item.label}
            </Link>
          ))}
        </nav>
      </div>
    </QueryClientProvider>
  )
}
