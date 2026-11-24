import { useCallback, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, createFileRoute } from '@tanstack/react-router'
import {
  apiGet,
  apiWrite,
  ApiError,
  type PositionCloseRequest,
  type PositionDetail,
} from '../lib/api'
import { brl, dateBr, fmt } from '../lib/format'
import { Dialog } from '../components/Dialog'
import { GlossaryMetric, Metric, MetricGrid } from '../components/Metric'
import { MetricModal } from '../components/MetricModal'
import type { ModalTarget, OpenMetric } from '../components/metricKeys'
import { PayoffChart } from '../components/PayoffChart'
import {
  formatPnlBrl,
  formatPnlPct,
  legTypeLabel,
  pnlTone,
  positionKindLabel,
  positionStatusLabel,
  predictionStatusLabel,
  PositionFlags,
} from '../components/PositionBits'
import { ErrorState, Loading } from '../components/State'
import { HelpTip } from '../components/HelpTip'
import { TokenGate, useAppToken } from '../components/TokenGate'
import { Button } from '../components/ui/button'
import { Input } from '../components/ui/input'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '../components/ui/table'

export const Route = createFileRoute('/carteira_/$positionId')({
  component: PositionDetailPage,
})

function percentPoints(value: number | null): string {
  return value == null ? '—' : `${fmt(value)}%`
}

function PositionDetailPage() {
  const { positionId } = Route.useParams()
  const auth = useAppToken()
  const queryClient = useQueryClient()
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [closeDate, setCloseDate] = useState('')
  const [tokenNotice, setTokenNotice] = useState<string | null>(null)
  const [writeNotice, setWriteNotice] = useState<string | null>(null)
  const [modal, setModal] = useState<ModalTarget | null>(null)
  const openMetric: OpenMetric = (field, label, value) => setModal({ field, label, value })
  const closeConfirm = useCallback(() => setConfirmOpen(false), [])

  const position = useQuery({
    queryKey: ['position', positionId],
    queryFn: ({ signal }) =>
      apiGet<PositionDetail>(`/api/positions/${encodeURIComponent(positionId)}`, signal),
    staleTime: 0,
    refetchOnMount: 'always',
  })

  const close = useMutation({
    mutationFn: (body: PositionCloseRequest) =>
      apiWrite<PositionDetail>(`/api/positions/${encodeURIComponent(positionId)}/close`, body),
    onSuccess: (result) => {
      queryClient.setQueryData(['position', positionId], result)
      void queryClient.invalidateQueries({ queryKey: ['positions'] })
      setWriteNotice(
        result.meta?.truncated
          ? 'O fechamento foi gravado, mas o detalhe retornado ficou truncado. A carteira será recarregada.'
          : result.meta?.note ?? null,
      )
      setConfirmOpen(false)
    },
    onError: (error) => {
      if (error instanceof ApiError && error.status === 401) {
        const cleared = auth.forgetToken()
        setTokenNotice(
          cleared
            ? 'O token foi recusado. Confira o APP_TOKEN do servidor e digite novamente.'
            : 'Não foi possível limpar o token deste navegador. Limpe os dados do site e tente novamente.',
        )
      } else if (
        error instanceof ApiError &&
        (error.status === 409 || error.code === 'position_closed')
      ) {
        setConfirmOpen(false)
        void queryClient.invalidateQueries({ queryKey: ['position', positionId] })
        void queryClient.invalidateQueries({ queryKey: ['positions'] })
      }
    },
  })

  function openConfirmation() {
    setCloseDate('')
    setConfirmOpen(true)
  }

  function changeToken() {
    if (auth.forgetToken()) setTokenNotice(null)
  }

  if (position.isLoading) return <Loading label="carregando posição…" />
  if (position.isError) return <ErrorState error={position.error} title="Posição indisponível" />
  if (!position.data) return <Loading label="carregando posição…" />

  const data = position.data.data
  const isOpen = data.status === 'open'
  const payoffTruncated = Boolean(position.data.meta?.truncated || position.data.meta?.note)

  return (
    <>
      <Link className="back lg:text-label" to="/carteira">
        <span aria-hidden="true">‹</span> Carteira
      </Link>

      <div className="position-detail__head">
        <div>
          <h1 className="screen-title lg:text-screen-title lg:leading-title">{data.underlying}</h1>
          <p className="quiet-line lg:text-label">
            {positionKindLabel(data.kind)} · {positionStatusLabel(data.status)} · sessão{' '}
            {position.data.as_of_session ?? '—'}
          </p>
        </div>
      </div>

      {writeNotice && (
        <p className="notice lg:text-label" role="status">
          {writeNotice}
        </p>
      )}
      {(position.data.meta?.truncated || position.data.meta?.note) && (
        <p className="notice lg:text-label" role="status">
          {position.data.meta.note ??
            'O payoff foi truncado no transporte e não é exibido como completo. Reabra a posição para tentar novamente.'}
        </p>
      )}

      <p className="position-flags-line lg:text-label">
        <span className="quiet lg:text-label">Flags da posição</span>{' '}
        <PositionFlags flags={data.flags} />
      </p>

      <MetricGrid>
        <Metric
          label="P&L"
          value={<span className={pnlTone(data.pnl_brl)}>{formatPnlBrl(data.pnl_brl)}</span>}
          emphasis
        />
        <GlossaryMetric
          field="pnl_pct"
          label="P&L %"
          value={formatPnlPct(data.pnl_pct)}
          onOpen={openMetric}
        />
        <GlossaryMetric
          field="delta"
          label="Delta"
          value={fmt(data.delta, 3)}
          onOpen={openMetric}
        />
        <GlossaryMetric
          field="theta_brl"
          label="Theta (R$/dia)"
          value={brl(data.theta_brl)}
          onOpen={openMetric}
        />
        <GlossaryMetric
          field="vega_brl"
          label="Vega (R$/ponto)"
          value={brl(data.vega_brl)}
          onOpen={openMetric}
        />
        <GlossaryMetric
          field="days_to_expiry"
          label="Sessões até vencimento"
          value={fmt(data.days_to_expiry, 0)}
          onOpen={openMetric}
        />
        <GlossaryMetric
          field="breakeven_brl"
          label="Breakevens"
          value={
            data.breakeven_brl.length > 0
              ? data.breakeven_brl.map((value) => brl(value)).join(' · ')
              : '—'
          }
          onOpen={openMetric}
        />
        <GlossaryMetric
          field="entry_iv_atm_30d"
          label="Volatilidade implícita 30d na entrada"
          value={percentPoints(data.entry_iv_atm_30d)}
          onOpen={openMetric}
        />
        <GlossaryMetric
          field="current_iv_atm_30d"
          label={
            isOpen
              ? 'Volatilidade implícita 30d na sessão'
              : 'Volatilidade implícita 30d no fechamento'
          }
          value={percentPoints(data.current_iv_atm_30d)}
          onOpen={openMetric}
        />
      </MetricGrid>

      <section className="position-section" aria-labelledby="position-identity-title">
        <h2 className="block-title lg:text-block-title lg:leading-title" id="position-identity-title">Registro</h2>
        <dl className="identity">
          <div>
            <dt className="lg:text-label">Capital comprometido</dt>
            <dd className="num lg:text-detail">{brl(data.capital_committed_brl)}</dd>
          </div>
          <div>
            <dt className="lg:text-label">Limite de capital</dt>
            <dd className="num lg:text-detail">{brl(data.capital_limit_brl)}</dd>
          </div>
          <div>
            <dt className="lg:text-label">Aberta em</dt>
            <dd className="num lg:text-detail">{dateBr(data.opened_session)}</dd>
          </div>
          <div>
            <dt className="lg:text-label">Fechada em</dt>
            <dd className="num lg:text-detail">{dateBr(data.closed_session)}</dd>
          </div>
          <div>
            <dt className="lg:text-label">Vencimento</dt>
            <dd className="num lg:text-detail">{dateBr(data.maturity)}</dd>
          </div>
          <div>
            <dt className="lg:text-label">Status</dt>
            <dd className="lg:text-detail">{positionStatusLabel(data.status)}</dd>
          </div>
        </dl>
        {data.notes && <p className="position-detail__notes lg:text-body lg:leading-body">{data.notes}</p>}
      </section>

      <section className="position-section" aria-labelledby="position-legs-title">
        <h2 className="block-title lg:text-block-title lg:leading-title" id="position-legs-title">
          Pernas registradas
          <HelpTip
            label="O que é uma perna?"
            text="Perna é cada operação que compõe a posição: uma ação ou uma opção, com lado (comprada/vendida) e quantidade. Uma coberta, por exemplo, tem duas pernas: a ação comprada e a call vendida."
          />
        </h2>
        <div
          className="table-wrap table-wrap--position"
          role="region"
          tabIndex={0}
          aria-label="Tabela horizontal das pernas registradas"
        >
          <Table contained={false} className="screen-table position-table lg:text-table lg:leading-table">
            <TableHeader>
              <TableRow>
                <TableHead className="font-normal lg:text-table-head">Ticker</TableHead>
                <TableHead className="font-normal lg:text-table-head">Tipo</TableHead>
                <TableHead className="font-normal lg:text-table-head">Lado</TableHead>
                <TableHead className="font-normal lg:text-table-head">Ações</TableHead>
                <TableHead className="font-normal lg:text-table-head">Vencimento</TableHead>
                <TableHead className="font-normal lg:text-table-head">Entrada</TableHead>
                <TableHead className="font-normal lg:text-table-head">Strike</TableHead>
                <TableHead className="font-normal lg:text-table-head">Volatilidade implícita na entrada</TableHead>
                <TableHead className="font-normal lg:text-table-head">Delta entrada</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.legs.map((leg) => (
                <TableRow key={leg.id}>
                  <TableCell className="px-2 py-2.5">{leg.ticker}</TableCell>
                  <TableCell className="px-2 py-2.5">{legTypeLabel(leg.type, leg.option_side)}</TableCell>
                  <TableCell className="px-2 py-2.5">{leg.side === 'long' ? 'comprada' : 'vendida'}</TableCell>
                  <TableCell className="px-2 py-2.5">{fmt(leg.qty, 0)}</TableCell>
                  <TableCell className="px-2 py-2.5">{dateBr(leg.maturity)}</TableCell>
                  <TableCell className="px-2 py-2.5">{brl(leg.entry_price_brl)}</TableCell>
                  <TableCell className="px-2 py-2.5">{brl(leg.entry_strike_brl)}</TableCell>
                  <TableCell className="px-2 py-2.5">{percentPoints(leg.entry_iv_pct)}</TableCell>
                  <TableCell className="px-2 py-2.5">{fmt(leg.entry_delta, 3)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      </section>

      <section className="position-section" aria-labelledby="position-marks-title">
        <h2 className="block-title lg:text-block-title lg:leading-title" id="position-marks-title">Marcação da sessão</h2>
        <div
          className="table-wrap table-wrap--position"
          role="region"
          tabIndex={0}
          aria-label="Tabela horizontal da marcação da sessão"
        >
          <Table contained={false} className="screen-table position-table position-table--marks lg:text-table lg:leading-table">
            <TableHeader>
              <TableRow>
                <TableHead className="font-normal lg:text-table-head">Ticker</TableHead>
                <TableHead className="font-normal lg:text-table-head">Marca</TableHead>
                <TableHead className="font-normal lg:text-table-head">Strike vigente</TableHead>
                <TableHead className="font-normal lg:text-table-head">Volatilidade implícita</TableHead>
                <TableHead className="font-normal lg:text-table-head">Delta</TableHead>
                <TableHead className="font-normal lg:text-table-head">Gamma</TableHead>
                <TableHead className="font-normal lg:text-table-head">Theta</TableHead>
                <TableHead className="font-normal lg:text-table-head">Vega</TableHead>
                <TableHead className="font-normal lg:text-table-head">Spread</TableHead>
                <TableHead className="font-normal lg:text-table-head">Spread %</TableHead>
                <TableHead className="font-normal lg:text-table-head">Um lado</TableHead>
                <TableHead className="font-normal lg:text-table-head">Flags</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.marks.map((mark) => (
                <TableRow key={mark.leg_id}>
                  <TableCell className="px-2 py-2.5">{mark.ticker}</TableCell>
                  <TableCell className="px-2 py-2.5">{brl(mark.mark_brl)}</TableCell>
                  <TableCell className="px-2 py-2.5">{brl(mark.strike_brl)}</TableCell>
                  <TableCell className="px-2 py-2.5">{percentPoints(mark.iv_pct)}</TableCell>
                  <TableCell className="px-2 py-2.5">{fmt(mark.delta, 3)}</TableCell>
                  <TableCell className="px-2 py-2.5">{fmt(mark.gamma, 3)}</TableCell>
                  <TableCell className="px-2 py-2.5">{brl(mark.theta_brl)}</TableCell>
                  <TableCell className="px-2 py-2.5">{brl(mark.vega_brl)}</TableCell>
                  <TableCell className="px-2 py-2.5">{brl(mark.spread_brl)}</TableCell>
                  <TableCell className="px-2 py-2.5">{percentPoints(mark.spread_pct)}</TableCell>
                  <TableCell className="px-2 py-2.5">{mark.one_sided ? 'sim' : 'não'}</TableCell>
                  <TableCell className="position-table__flags px-2 py-2.5">
                    <PositionFlags flags={mark.flags} emptyLabel="—" />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      </section>

      <section className="position-section" aria-labelledby="position-thesis-title">
        <h2 className="block-title lg:text-block-title lg:leading-title" id="position-thesis-title">Tese vs realidade</h2>
        <div className="position-thesis">
          <p className="position-thesis__line lg:text-body lg:leading-body">
            {data.thesis_line ?? 'Nenhuma linha de tese retornada para esta posição.'}
          </p>
          {data.prediction ? (
            <dl className="identity">
              <div>
                <dt className="lg:text-label">Métrica</dt>
                <dd className="num lg:text-detail">{data.prediction.metric}</dd>
              </div>
              <div>
                <dt className="lg:text-label">Condição</dt>
                <dd className="num lg:text-detail">
                  {data.prediction.operator} {fmt(data.prediction.value)}
                </dd>
              </div>
              <div>
                <dt className="lg:text-label">Horizonte</dt>
                <dd className="num lg:text-detail">{fmt(data.prediction.horizon_sessions, 0)} sessões</dd>
              </div>
              <div>
                <dt className="lg:text-label">Avaliar em</dt>
                <dd className="num lg:text-detail">{dateBr(data.prediction.evaluate_on)}</dd>
              </div>
              <div>
                <dt className="lg:text-label">Status</dt>
                <dd className="lg:text-detail">{predictionStatusLabel(data.prediction.status)}</dd>
              </div>
              <div>
                <dt className="lg:text-label">Observado</dt>
                <dd className="num lg:text-detail">{fmt(data.prediction.observed)}</dd>
              </div>
            </dl>
          ) : (
            <p className="note lg:text-label">Sem previsão estruturada; esta posição não entra na calibração.</p>
          )}
        </div>
      </section>

      <section className="position-section" aria-labelledby="position-payoff-title">
        <h2 className="block-title lg:text-block-title lg:leading-title" id="position-payoff-title">Payoff no vencimento</h2>
        <p className="chain-note lg:text-label lg:leading-body">Preços de entrada do journal; o backend calcula todos os pontos.</p>
        {!payoffTruncated && (
          <PayoffChart
            points={data.payoff}
            breakevens={data.breakeven_brl}
            ariaLabel={`Payoff no vencimento da posição de ${data.underlying}`}
          />
        )}
      </section>

      {isOpen && (
        <section className="position-write" aria-labelledby="position-close-title">
          <h2 className="block-title lg:text-block-title lg:leading-title" id="position-close-title">Fechamento</h2>
          {!auth.hasToken && !confirmOpen && (
            <TokenGate compact notice={tokenNotice} onUnlock={() => setTokenNotice(null)} />
          )}
          <div className="position-detail__actions">
            <Button
              id="close-position-trigger"
              type="button"
              onClick={openConfirmation}
              disabled={!auth.hasToken}
            >
              Fechar posição
            </Button>
            {auth.hasToken && auth.tokenRequired && (
              <Button type="button" variant="link" size="sm" className="h-[44px] px-0 lg:text-label" onClick={changeToken}>
                Trocar token
              </Button>
            )}
          </div>
        </section>
      )}

      <MetricModal target={modal} onClose={() => setModal(null)} />

      <Dialog
        open={confirmOpen}
        title="Fechar posição"
        eyebrow="evento append-only"
        description="O fechamento usa a marcação EOD e acrescenta um evento que não pode ser desfeito."
        onClose={closeConfirm}
        busy={close.isPending}
        returnFocusSelector="#close-position-trigger"
      >
        {confirmOpen && (
          <>
            {!auth.hasToken && (
              <TokenGate
                compact
                autoFocus
                notice={tokenNotice}
                onUnlock={() => setTokenNotice(null)}
              />
            )}
            <label className="builder__field">
              <span className="builder__field-label lg:text-label">Data da sessão (opcional)</span>
              <Input
                type="date"
                value={closeDate}
                onChange={(event) => setCloseDate(event.target.value)}
                data-autofocus
                disabled={close.isPending}
              />
            </label>
            <p className="note lg:text-label">Em branco, o backend usa a última sessão disponível.</p>
            {close.isError && <ErrorState error={close.error} title="Fechamento não registrado" />}
            <div className="position-form__actions">
              <Button
                type="button"
                variant="outline"
                onClick={closeConfirm}
                disabled={close.isPending}
              >
                Cancelar
              </Button>
              <Button
                type="button"
                onClick={() => close.mutate(closeDate ? { date: closeDate } : {})}
                disabled={close.isPending}
              >
                {close.isPending ? 'fechando…' : 'Confirmar fechamento'}
              </Button>
            </div>
          </>
        )}
      </Dialog>
    </>
  )
}
