const STAMP =
  'ml-auto min-w-0 whitespace-normal text-right font-mono text-[calc(11px*var(--font-scale))] text-[var(--text-2)] lg:text-label'

/** Carimbo da sessão: vem de /api/health, nunca do relógio do navegador.
 *
 * `age_sessions` é o atraso em pregões contra o esperado (0 = D-1 do arquivo).
 * O sufixo é derivado dele, não fixo: `D-1`, `D-{n+1} · atrasada` ou só a data
 * quando o campo não vem. Offline não se sabe a idade — mostra a data
 * persistida + "offline" e nenhum `D-n`.
 */
export function Stamp({
  session,
  loading,
  ageSessions,
  offline,
}: {
  session?: string | null
  loading?: boolean
  ageSessions?: number | null
  offline?: boolean
}) {
  if (loading) return <span className={STAMP}>carregando sessão…</span>
  if (!session) {
    return <span className={STAMP}>{offline ? 'offline · sessão indisponível' : 'sessão indisponível'}</span>
  }
  if (offline) {
    return (
      <span className={STAMP}>
        sessão <strong className="whitespace-nowrap font-medium text-primary">{session}</strong>
        <span className="px-[6px] text-muted-foreground">·</span>offline
      </span>
    )
  }
  const age = typeof ageSessions === 'number' ? ageSessions : null
  return (
    <span className={STAMP}>
      sessão <strong className="whitespace-nowrap font-medium text-primary">{session}</strong>
      {age != null && (
        <>
          <span className="px-[6px] text-muted-foreground">·</span>
          {age === 0 ? 'D-1' : `D-${age + 1} · atrasada`}
        </>
      )}
    </span>
  )
}
