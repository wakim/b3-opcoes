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
  positionKindLabel,
  positionStatusLabel,
  PositionFlags,
} from '../components/PositionBits'
import { PositionForm } from '../components/PositionForm'
import { ErrorState, Loading } from '../components/State'
import { TokenGate, useAppToken } from '../components/TokenGate'
import { Button } from '../components/ui/button'

export const Route = createFileRoute('/carteira')({
  component: Carteira,
})

function Carteira() {
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
      <div className="portfolio-head">
        <div>
          <h1 className="screen-title lg:text-screen-title lg:leading-title">Carteira</h1>
          <p className="quiet-line lg:text-label">Posições e marcação EOD do journal</p>
        </div>
        {auth.hasToken && (
          <div className="portfolio-actions">
            <Button
              id="new-position-trigger"
              type="button"
              onClick={() => {
                setWriteNotice(null)
                setFormBusy(false)
                setFormOpen(true)
              }}
            >
              Nova posição
            </Button>
            {auth.tokenRequired && (
              <Button type="button" variant="link" size="sm" className="h-[44px] px-0 lg:text-label" onClick={changeToken}>
                Trocar token
              </Button>
            )}
          </div>
        )}
      </div>

      {!auth.hasToken && (
        <TokenGate notice={tokenNotice} onUnlock={() => setTokenNotice(null)} />
      )}

      {portfolio.isLoading && <Loading label="carregando carteira…" />}
      {portfolio.isError && <ErrorState error={portfolio.error} title="Carteira indisponível" />}

      {writeNotice && (
        <p className="notice lg:text-label" role="status">
          {writeNotice}
        </p>
      )}

      {portfolio.data && (
        <>
          <p className="quiet-line lg:text-label">
            sessão {portfolio.data.data.session} · {portfolio.data.data.totals.open_positions}{' '}
            {portfolio.data.data.totals.open_positions === 1 ? 'posição aberta' : 'posições abertas'}
          </p>

          {(portfolio.data.meta?.truncated || portfolio.data.meta?.note) && (
            <p className="notice lg:text-label" role="status">
              {portfolio.data.meta.note ??
                `Mostrando ${portfolio.data.meta.returned} de ${portfolio.data.meta.total} posições abertas.`}
            </p>
          )}

          <PositionTotalsGrid totals={portfolio.data.data.totals} onMetric={openMetric} />

          <div className="block-head">
            <h2 className="block-title lg:text-block-title lg:leading-title">Posições</h2>
            <span className="block-count lg:text-label">{portfolio.data.data.positions.length}</span>
          </div>

          {portfolio.data.data.positions.length === 0 &&
          (portfolio.data.meta?.total ?? 0) === 0 ? (
            <div className="position-empty">
              <p>Nenhuma posição aberta.</p>
              <p className="note lg:text-label">
                {auth.hasToken
                  ? 'Use “Nova posição” para registrar pernas e a marcação de entrada.'
                  : 'Salve o token acima e use “Nova posição” para registrar pernas e a marcação de entrada.'}
              </p>
            </div>
          ) : portfolio.data.data.positions.length === 0 ? (
            <div className="position-empty">
              <p>Existem posições abertas, mas nenhuma pôde ser transportada nesta resposta.</p>
              <p className="note lg:text-label">Recarregue a carteira; o backend não inventou uma lista vazia.</p>
            </div>
          ) : (
            <div className="position-list">
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
        title="Nova posição"
        eyebrow="journal append-only"
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
          label="Capital comprometido"
          value={brl(totals.capital_committed_brl)}
          onOpen={onMetric}
        />
        <Metric
          label="P&L"
          value={<span className={pnlTone(totals.pnl_brl)}>{formatPnlBrl(totals.pnl_brl)}</span>}
          emphasis
        />
        <GlossaryMetric
          field="pnl_pct"
          label="P&L %"
          value={formatPnlPct(totals.pnl_pct)}
          onOpen={onMetric}
        />
        <GlossaryMetric
          field="delta"
          label="Delta"
          value={fmt(totals.delta, 3)}
          onOpen={onMetric}
        />
        <GlossaryMetric
          field="delta_exposure_brl"
          label="Delta em R$"
          value={brl(totals.delta_exposure_brl)}
          onOpen={onMetric}
        />
        <GlossaryMetric
          field="gamma"
          label="Gamma"
          value={fmt(totals.gamma, 3)}
          onOpen={onMetric}
        />
        <GlossaryMetric
          field="theta_brl"
          label="Theta (R$/dia)"
          value={brl(totals.theta_brl)}
          onOpen={onMetric}
        />
        <GlossaryMetric
          field="vega_brl"
          label="Vega (R$/ponto)"
          value={brl(totals.vega_brl)}
          onOpen={onMetric}
        />
      </MetricGrid>
      <p className="position-flags-line lg:text-label">
        <span className="quiet lg:text-label">Flags da carteira</span>{' '}
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
  const breakevens = position.breakeven_brl

  return (
    <article className="position-card">
      <header className="position-card__head">
        <div>
          <Link
            className="position-card__title lg:text-metric"
            to="/carteira/$positionId"
            params={{ positionId: position.id }}
          >
            {position.underlying}
          </Link>
          <span className="position-card__kind lg:text-label">{positionKindLabel(position.kind)}</span>
        </div>
        <span className="quiet lg:text-label">{positionStatusLabel(position.status)}</span>
      </header>

      <div className="position-card__meta lg:text-label">
        <span>aberta em {dateBr(position.opened_session)}</span>
        {position.closed_session && <span>fechada em {dateBr(position.closed_session)}</span>}
        {position.maturity && <span>vencimento {dateBr(position.maturity)}</span>}
        <span>limite {brl(position.capital_limit_brl)}</span>
      </div>

      <MetricGrid>
        <Metric
          label="P&L"
          value={<span className={pnlTone(position.pnl_brl)}>{formatPnlBrl(position.pnl_brl)}</span>}
          emphasis
        />
        <GlossaryMetric
          field="pnl_pct"
          label="P&L %"
          value={formatPnlPct(position.pnl_pct)}
          onOpen={onMetric}
        />
        <GlossaryMetric
          field="delta"
          label="Delta"
          value={fmt(position.delta, 3)}
          onOpen={onMetric}
        />
        <GlossaryMetric
          field="theta_brl"
          label="Theta (R$/dia)"
          value={brl(position.theta_brl)}
          onOpen={onMetric}
        />
        <GlossaryMetric
          field="vega_brl"
          label="Vega (R$/ponto)"
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

      <div className="position-card__row lg:text-label">
        <span className="quiet lg:text-label">Breakevens</span>
        <span className="num">
          {breakevens.length > 0 ? breakevens.map((value) => brl(value)).join(' · ') : '—'}
        </span>
      </div>

      {position.notes && <p className="position-card__notes lg:text-body lg:leading-body">{position.notes}</p>}

      <div className="position-card__row lg:text-label">
        <span className="quiet lg:text-label">Flags</span>
        <PositionFlags flags={position.flags} />
      </div>

      <Link
        className="position-card__link lg:text-label"
        to="/carteira/$positionId"
        params={{ positionId: position.id }}
      >
        Abrir posição <span aria-hidden="true">›</span>
      </Link>
    </article>
  )
}
