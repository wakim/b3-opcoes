import { useCallback, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, createFileRoute } from '@tanstack/react-router'
import {
  fetchPositionsAll,
  type Envelope,
  type PositionDetail,
  type PositionsData,
  type PositionSummary,
  type PositionTotals,
} from '../lib/api'
import { brl, dateBr, fmt } from '../lib/format'
import { Dialog } from '../components/Dialog'
import { GlossaryMetric, Metric, MetricGrid } from '../components/Metric'
import { MetricModal } from '../components/MetricModal'
import type { ModalTarget, OpenMetric } from '../components/metricKeys'
import {
  formatPnlBrl,
  formatPnlPct,
  pnlTone,
  PositionFlags,
} from '../components/PositionBits'
import { usePresentation } from '../lib/presentation'
import { PositionForm } from '../components/PositionForm'
import { ErrorState, Loading } from '../components/State'
import { TokenGate, useAppToken } from '../components/TokenGate'
import { Button } from '../components/ui/button'
import {
  blockCount,
  blockHead,
  blockTitleBase,
  notice,
  noteBase,
  num,
  quiet,
  quietLine,
  screenTitle,
} from '../lib/classes'

const portfolioHead = 'flex flex-wrap items-start justify-between gap-[12px] max-sm:block'
const actionsRow = 'flex flex-wrap items-center gap-[12px] max-sm:mt-[12px]'
const positionCard =
  'rounded-[6px] border border-border bg-card p-[16px] max-sm:p-[12px]'
const positionRow =
  'grid grid-cols-[minmax(112px,0.4fr)_minmax(0,1fr)] gap-[12px] border-t border-t-border py-[12px] text-[calc(11.5px*var(--font-scale))] max-sm:grid-cols-[minmax(0,1fr)] max-sm:gap-[4px]'

export const Route = createFileRoute('/carteira')({
  component: Carteira,
})

function Carteira() {
  const p = usePresentation()
  const auth = useAppToken()
  const queryClient = useQueryClient()
  const [formOpen, setFormOpen] = useState(false)
  const [formBusy, setFormBusy] = useState(false)
  const [tokenNotice, setTokenNotice] = useState<string | null>(null)
  const [writeNotice, setWriteNotice] = useState<string | null>(null)
  const [modal, setModal] = useState<ModalTarget | null>(null)
  const openMetric: OpenMetric = (field, label, value) => setModal({ field, label, value })
  const closeForm = useCallback(() => setFormOpen(false), [])

  const portfolio = useQuery({
    queryKey: ['positions', 'all'],
    queryFn: ({ signal }) => fetchPositionsAll(signal),
  })

  function handleUnauthorized() {
    const cleared = auth.forgetToken()
    setTokenNotice(
      cleared
        ? 'O token foi recusado. Confira o APP_TOKEN do servidor e digite novamente.'
        : 'Não foi possível limpar o token deste navegador. Limpe os dados do site e tente novamente.',
    )
  }

  async function handleCreated(result: Envelope<PositionDetail>) {
    const position = result.data
    await queryClient.cancelQueries({ queryKey: ['positions'] })
    await queryClient.invalidateQueries({ queryKey: ['positions'] })
    const refreshed = queryClient.getQueryData<Envelope<PositionsData>>(['positions', 'all'])
    const loaded = refreshed?.data.positions.some((row) => row.id === position.id) ?? false
    setWriteNotice(
      !loaded
        ? 'A posição foi gravada, mas a carteira não recarregou. Recarregue a página antes de tentar outra abertura.'
        : result.meta?.truncated
          ? 'A posição foi gravada, mas o detalhe retornado ficou truncado. Abra a posição para reler o payoff.'
          : result.meta?.note ?? null,
    )
    setFormBusy(false)
    setFormOpen(false)
    setTokenNotice(null)
  }

  function changeToken() {
    if (auth.forgetToken()) {
      setFormOpen(false)
      setTokenNotice(null)
    }
  }

  return (
    <>
      <div className={portfolioHead}>
        <div>
          <h1 className={`${screenTitle} lg:text-screen-title lg:leading-title`}>Carteira</h1>
          <p className={`${quietLine} lg:text-label`}>Posições e marcação EOD do journal</p>
        </div>
        {auth.hasToken && (
          <div className={actionsRow}>
            <Button
              id="new-position-trigger"
              type="button"
              onClick={() => {
                setWriteNotice(null)
                setFormBusy(false)
                setFormOpen(true)
              }}
            >
              {p.copy('action', 'new_position').label}
            </Button>
            {auth.tokenRequired && (
              <Button type="button" variant="link" size="sm" className="h-[44px] px-0 lg:text-label" onClick={changeToken}>
                {p.copy('token', 'change').label}
              </Button>
            )}
          </div>
        )}
      </div>

      {!auth.hasToken && (
        <TokenGate notice={tokenNotice} onUnlock={() => setTokenNotice(null)} />
      )}

      {portfolio.isLoading && <Loading label={p.copy('loading', 'wallet').label} />}
      {portfolio.isError && <ErrorState error={portfolio.error} title="Carteira indisponível" />}

      {writeNotice && (
        <p className={`${notice} lg:text-label`} role="status">
          {writeNotice}
        </p>
      )}

      {portfolio.data && (
        <>
          <p className={`${quietLine} lg:text-label`}>
            sessão {portfolio.data.data.session} · {portfolio.data.data.totals.open_positions}{' '}
            {portfolio.data.data.totals.open_positions === 1 ? 'posição aberta' : 'posições abertas'}
          </p>

          {(portfolio.data.meta?.truncated || portfolio.data.meta?.note) && (
            <p className={`${notice} lg:text-label`} role="status">
              {portfolio.data.meta.note ??
                `Mostrando ${portfolio.data.meta.returned} de ${portfolio.data.meta.total} posições abertas.`}
            </p>
          )}

          <PositionTotalsGrid totals={portfolio.data.data.totals} onMetric={openMetric} />

          <div className={blockHead}>
            <h2 className={`${blockTitleBase} lg:text-block-title lg:leading-title`}>Posições</h2>
            <span className={`${blockCount} lg:text-label`}>{portfolio.data.data.positions.length}</span>
          </div>

          {portfolio.data.data.positions.length === 0 &&
          (portfolio.data.meta?.total ?? 0) === 0 ? (
            <div className="rounded-[6px] border border-dashed border-input bg-card px-[16px] py-[24px]">
              <p className="mb-[4px]">{p.copy('empty', 'positions').label}</p>
              <p className={`${noteBase} lg:text-label`}>
                {auth.hasToken
                  ? `Use “${p.copy('action', 'new_position').label}” para registrar pernas e a marcação de entrada.`
                  : `Salve o token acima e use “${p.copy('action', 'new_position').label}” para registrar pernas e a marcação de entrada.`}
              </p>
            </div>
          ) : portfolio.data.data.positions.length === 0 ? (
            <div className="rounded-[6px] border border-dashed border-input bg-card px-[16px] py-[24px]">
              <p className="mb-[4px]">Existem posições abertas, mas nenhuma pôde ser transportada nesta resposta.</p>
              <p className={`${noteBase} lg:text-label`}>Recarregue a carteira; o backend não inventou uma lista vazia.</p>
            </div>
          ) : (
            <div className="grid gap-[12px]">
              {portfolio.data.data.positions.map((position) => (
                <PositionCard
                  key={position.id}
                  position={position}
                  onMetric={openMetric}
                />
              ))}
            </div>
          )}
        </>
      )}

      <MetricModal target={modal} onClose={() => setModal(null)} />

      <Dialog
        open={formOpen}
        title={p.copy('action', 'new_position').label}
        eyebrow={p.copy('append_only').label}
        onClose={closeForm}
        busy={formBusy}
        showCloseButton={false}
        size="wide"
        returnFocusSelector="#new-position-trigger"
      >
        {formOpen && (
          <>
            {!auth.hasToken && (
              <TokenGate
                compact
                title="Token para registrar"
                autoFocus
                notice={tokenNotice}
                onUnlock={() => setTokenNotice(null)}
              />
            )}
            <PositionForm
              onCreated={handleCreated}
              onCancel={closeForm}
              onUnauthorized={handleUnauthorized}
              onBusyChange={setFormBusy}
            />
          </>
        )}
      </Dialog>
    </>
  )
}

function PositionTotalsGrid({
  totals,
  onMetric,
}: {
  totals: PositionTotals
  onMetric: OpenMetric
}) {
  return (
    <>
      <MetricGrid>
        <Metric label="Posições abertas" value={fmt(totals.open_positions, 0)} />
        <GlossaryMetric
          field="capital_committed_brl"
          term="capital"
          value={brl(totals.capital_committed_brl)}
          onOpen={onMetric}
        />
        <Metric
          term="pnl"
          value={<span className={pnlTone(totals.pnl_brl)}>{formatPnlBrl(totals.pnl_brl)}</span>}
          emphasis
        />
        <GlossaryMetric
          field="pnl_pct"
          term="pnl"
          variant="pct"
          value={formatPnlPct(totals.pnl_pct)}
          onOpen={onMetric}
        />
        <GlossaryMetric
          field="delta"
          term="delta"
          variant="aggregate"
          value={fmt(totals.delta, 3)}
          onOpen={onMetric}
        />
        <GlossaryMetric
          field="delta_exposure_brl"
          term="delta"
          variant="exposure"
          value={brl(totals.delta_exposure_brl)}
          onOpen={onMetric}
        />
        <GlossaryMetric
          field="gamma"
          term="gamma"
          variant="aggregate"
          value={fmt(totals.gamma, 3)}
          onOpen={onMetric}
        />
        <GlossaryMetric
          field="theta_brl"
          term="theta"
          variant="brl_day"
          value={brl(totals.theta_brl)}
          onOpen={onMetric}
        />
        <GlossaryMetric
          field="vega_brl"
          term="vega"
          variant="brl_point"
          value={brl(totals.vega_brl)}
          onOpen={onMetric}
        />
      </MetricGrid>
      <p className="mb-[16px] text-[calc(11.5px*var(--font-scale))] lg:text-label">
        <span className={`${quiet} lg:text-label`}>Flags da carteira</span>{' '}
        <PositionFlags flags={totals.flags} />
      </p>
    </>
  )
}

function PositionCard({
  position,
  onMetric,
}: {
  position: PositionSummary
  onMetric: OpenMetric
}) {
  const p = usePresentation()
  const breakevens = position.breakeven_brl

  return (
    <article className={positionCard}>
      <header className="flex flex-wrap items-baseline justify-between gap-x-[12px] gap-y-[4px]">
        <div>
          <Link
            className="inline-flex min-h-[44px] items-center font-mono text-[calc(20px*var(--font-scale))] font-medium text-foreground tabular-nums focus-visible:outline-2 focus-visible:outline-solid focus-visible:outline-offset-[2px] focus-visible:outline-primary lg:text-metric"
            to="/carteira/$positionId"
            params={{ positionId: position.id }}
          >
            {position.underlying}
          </Link>
          <span className="ml-[12px] text-[calc(12px*var(--font-scale))] text-[color:var(--text-2)] lg:text-label">{p.positionKind(position.kind).label}</span>
        </div>
        <span className={`${quiet} lg:text-label`}>{p.positionStatus(position.status)}</span>
      </header>

      <div className="mb-[12px] flex flex-wrap gap-x-[16px] gap-y-[4px] font-mono text-[calc(11px*var(--font-scale))] text-muted-foreground tabular-nums lg:text-label">
        <span>aberta em {dateBr(position.opened_session)}</span>
        {position.closed_session && <span>fechada em {dateBr(position.closed_session)}</span>}
        {position.maturity && <span>vencimento {dateBr(position.maturity)}</span>}
        <span>limite {brl(position.capital_limit_brl)}</span>
      </div>

      <MetricGrid inCard>
        <Metric
          term="pnl"
          value={<span className={pnlTone(position.pnl_brl)}>{formatPnlBrl(position.pnl_brl)}</span>}
          emphasis
        />
        <GlossaryMetric
          field="pnl_pct"
          term="pnl"
          variant="pct"
          value={formatPnlPct(position.pnl_pct)}
          onOpen={onMetric}
        />
        <GlossaryMetric
          field="delta"
          term="delta"
          value={fmt(position.delta, 3)}
          onOpen={onMetric}
        />
        <GlossaryMetric
          field="theta_brl"
          term="theta"
          variant="brl_day"
          value={brl(position.theta_brl)}
          onOpen={onMetric}
        />
        <GlossaryMetric
          field="vega_brl"
          term="vega"
          variant="brl_point"
          value={brl(position.vega_brl)}
          onOpen={onMetric}
        />
        <GlossaryMetric
          field="days_to_expiry"
          label="Sessões até vencimento"
          value={fmt(position.days_to_expiry, 0)}
          onOpen={onMetric}
        />
      </MetricGrid>

      <div className={`${positionRow} lg:text-label`}>
        <span className={`${quiet} lg:text-label`}>Breakevens</span>
        <span className={`${num} text-right`}>
          {breakevens.length > 0 ? breakevens.map((value) => brl(value)).join(' · ') : '—'}
        </span>
      </div>

      {position.notes && <p className="border-t border-t-border py-[12px] text-[calc(12.5px*var(--font-scale))] leading-[1.55] [overflow-wrap:anywhere] whitespace-pre-wrap text-[color:var(--text-2)] lg:text-body lg:leading-body">{position.notes}</p>}

      <div className={`${positionRow} lg:text-label`}>
        <span className={`${quiet} lg:text-label`}>Flags</span>
        <PositionFlags flags={position.flags} />
      </div>

      <Link
        className="inline-flex min-h-[44px] items-center gap-[8px] text-[calc(12px*var(--font-scale))] text-[color:var(--text-2)] underline underline-offset-[2px] focus-visible:outline-2 focus-visible:outline-solid focus-visible:outline-offset-[2px] focus-visible:outline-primary lg:text-label"
        to="/carteira/$positionId"
        params={{ positionId: position.id }}
      >
        {p.copy('action', 'open_position').label} <span className="font-mono text-[calc(18px*var(--font-scale))] leading-none text-muted-foreground" aria-hidden="true">›</span>
      </Link>
    </article>
  )
}
