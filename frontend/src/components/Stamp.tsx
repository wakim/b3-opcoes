/** Carimbo da sessão: vem de /api/health, nunca do relógio do navegador. */
export function Stamp({ session, loading }: { session?: string | null; loading?: boolean }) {
  if (loading) return <span className="stamp">carregando sessão…</span>
  if (!session) return <span className="stamp">sessão indisponível</span>
  return (
    <span className="stamp">
      sessão <strong>{session}</strong>
      <span className="stamp__sep">·</span>D-1
    </span>
  )
}
