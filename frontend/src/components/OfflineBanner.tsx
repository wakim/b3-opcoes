/** Estado de rede e última sessão conhecida (PWA offline, F5.7).
 *
 * A sessão exibida offline vem de `/api/health`, não do relógio: o último
 * sucesso é persistido em `localStorage` a cada resposta. Sem valor salvo, o
 * aviso diz "sessão indisponível" — nunca inventa data.
 */
import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { apiGet, type Health } from '../lib/api'

const STORAGE_KEY = 'b3o:last-health'

export interface LastHealth {
  session: string
  ageSessions: number | null
}

/** Grava a última sessão vista com sucesso. Nunca usa `new Date()`. */
export function persistHealth(health: Health): LastHealth | null {
  if (!health.last_session) return null
  const value: LastHealth = {
    session: health.last_session,
    ageSessions: typeof health.age_sessions === 'number' ? health.age_sessions : null,
  }
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(value))
  } catch {
    // modo privado / storage cheio: o banner cai para "sessão indisponível"
  }
  return value
}

export function readPersistedHealth(): LastHealth | null {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as Partial<LastHealth>
    if (typeof parsed.session !== 'string' || parsed.session.length === 0) return null
    return {
      session: parsed.session,
      ageSessions: typeof parsed.ageSessions === 'number' ? parsed.ageSessions : null,
    }
  } catch {
    return null
  }
}

/** `true` quando o navegador está sem rede ou o servidor não responde.
 *
 * `navigator.onLine` sozinho não pega servidor parado (a rede continua "up");
 * por isso existe uma sonda `GET /api/health?probe=1` que o service worker
 * NÃO cacheia. A sonda é a única chamada que sai do NetworkFirst.
 */
export function useConnectivity(): boolean {
  const queryClient = useQueryClient()
  const [online, setOnline] = useState(() =>
    typeof navigator === 'undefined' ? true : navigator.onLine,
  )

  useEffect(() => {
    const up = () => {
      setOnline(true)
      void queryClient.invalidateQueries({ queryKey: ['health'] })
    }
    const down = () => setOnline(false)
    window.addEventListener('online', up)
    window.addEventListener('offline', down)
    return () => {
      window.removeEventListener('online', up)
      window.removeEventListener('offline', down)
    }
  }, [queryClient])

  const probe = useQuery({
    queryKey: ['health', 'probe'],
    queryFn: ({ signal }) => apiGet<Health>('/api/health?probe=1', signal),
    retry: false,
    staleTime: 0,
    refetchInterval: 15_000,
    refetchOnWindowFocus: false,
  })

  return !online || probe.isError
}

export function OfflineBanner({ offline, session }: { offline: boolean; session: string | null }) {
  if (!offline) return null
  return (
    <p className="offline-banner" role="status">
      sem rede —{' '}
      {session ? (
        <>
          última sessão <strong className="num">{session}</strong>
        </>
      ) : (
        'sessão indisponível'
      )}
    </p>
  )
}
