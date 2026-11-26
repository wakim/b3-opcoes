import { usePresentation } from '../lib/presentation'

const STAMP =
  'min-w-0 whitespace-normal text-right font-mono text-[calc(11px*var(--font-scale))] text-[var(--text-2)] lg:text-label'

/** Carimbo da sessão: vem de /api/health, nunca do relógio do navegador.
 *
 * `age_sessions` é o atraso em pregões contra o esperado (0 = D-1 do arquivo).
 * O sufixo é derivado dele, não fixo, e vem do dicionário (`stamp_age`).
 * Offline não se sabe a idade — mostra a data persistida + "offline" e nenhum
 * `D-n`.
 */
const SESSION_TOKEN = '{session}'

/** Separa o template do dicionário para destacar a sessão sem mudar a geometria. */
function stampParts(template: string): { before: string; tail: string } {
  const at = template.indexOf(SESSION_TOKEN)
  if (at < 0) return { before: template, tail: '' }
  const after = template.slice(at + SESSION_TOKEN.length)
  const dot = after.lastIndexOf('·')
  return {
    before: template.slice(0, at),
    tail: dot < 0 ? '' : after.slice(dot + 1).trim(),
  }
}

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
  const p = usePresentation()
  if (loading) return <span className={STAMP}>{p.copy('stamp', 'loading').label}</span>
  if (!session) {
    return <span className={STAMP}>{p.copy('stamp', 'unavailable').label}</span>
  }
  const subject = <strong className="whitespace-nowrap font-medium text-primary">{session}</strong>
  if (offline) {
    // Nunca mostra D-n: sem rede não há idade verificável. Ordem: sessão · offline.
    const { before, tail } = stampParts(p.copy('stamp', 'offline').label)
    return (
      <span className={STAMP}>
        {before}
        {subject}
        <span className="px-[6px] text-muted-foreground">·</span>
        {tail}
      </span>
    )
  }
  const { before } = stampParts(p.copy('stamp', 'online').label)
  const age = typeof ageSessions === 'number' ? ageSessions : null
  return (
    <span className={STAMP}>
      {before}
      {subject}
      {age != null && (
        <>
          <span className="px-[6px] text-muted-foreground">·</span>
          {p.copy('stamp_age', age === 0 ? 'current' : 'delayed', { n: age + 1, age: age + 1 }).label}
        </>
      )}
    </span>
  )
}
