import { useEffect, useState } from 'react'
import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query'
import { Link, Outlet, createRootRoute, useRouterState } from '@tanstack/react-router'
import { apiGet, type Health } from '../lib/api'
import { ErrorState } from '../components/State'
import { Stamp } from '../components/Stamp'
import { LanguageProvider } from '../lib/language'
import { LanguageToggle } from '../components/LanguageToggle'
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
      <LanguageProvider>
        <AppShell />
      </LanguageProvider>
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
    <div className="flex min-h-dvh flex-col overflow-x-clip">
      <header className="sticky top-0 z-10 flex flex-wrap items-center justify-between gap-x-[12px] gap-y-[4px] border-b border-border bg-background px-[16px] py-[12px]">
        <span className="shrink-0 text-[calc(16px*var(--font-scale))] font-medium tracking-normal lg:text-block-title">
          Opções B3
        </span>
        <div className="ml-auto flex min-w-0 flex-wrap items-center justify-end gap-[12px]">
          <LanguageToggle />
          <Stamp
            session={shownSession}
            loading={health.isLoading && !offline}
            ageSessions={ageSessions}
            offline={offline}
          />
        </div>
      </header>
      <main className="mx-auto w-full max-w-[900px] flex-1 px-[16px] pb-[88px] pt-[16px] lg:max-w-[1334px]">
        <OfflineBanner offline={offline} session={knownSession} />
        {health.isError && !offline && (
          <ErrorState error={health.error} title="Saúde do lake indisponível" />
        )}
        <Outlet />
      </main>
      <nav
        className="fixed inset-x-0 bottom-0 z-10 grid grid-cols-[repeat(4,1fr)] border-t border-border bg-card"
        aria-label="Navegação principal"
      >
        {NAV.map((item) => (
          <Link
            key={item.to}
            to={item.to}
            className="flex min-h-[48px] flex-col items-center justify-center gap-[4px] text-[calc(11px*var(--font-scale))] text-muted-foreground focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-primary aria-[current=page]:bg-secondary aria-[current=page]:text-primary lg:text-label"
            aria-current={pathname === item.to ? 'page' : undefined}
          >
            {item.label}
          </Link>
        ))}
      </nav>
    </div>
  )
}
