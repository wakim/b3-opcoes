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
  if (loading) return <span className="stamp">carregando sessão…</span>
  if (!session) {
    return <span className="stamp">{offline ? 'offline · sessão indisponível' : 'sessão indisponível'}</span>
  }
  if (offline) {
    return (
      <span className="stamp">
        sessão <strong>{session}</strong>
        <span className="stamp__sep">·</span>offline
      </span>
    )
  }
  const age = typeof ageSessions === 'number' ? ageSessions : null
  return (
    <span className="stamp">
      sessão <strong>{session}</strong>
      {age != null && (
        <>
          <span className="stamp__sep">·</span>
          {age === 0 ? 'D-1' : `D-${age + 1} · atrasada`}
        </>
      )}
    </span>
  )
}
