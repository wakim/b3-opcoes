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
import { ScreenTable } from '../components/ScreenTable'
import { TokenGate, useAppToken } from '../components/TokenGate'
import { Button } from '../components/ui/button'
import { Input } from '../components/ui/input'
import {
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '../components/ui/table'
import {
  back,
  blockTitle,
  chainNote,
  field,
  fieldLabel,
  focusInset,
  notice,
  note,
  num,
  quiet,
  quietLine,
  screenTitle,
} from '../lib/classes'

const identityGrid =
  'grid grid-cols-[repeat(2,minmax(0,1fr))] gap-[1px] overflow-hidden rounded-[6px] border border-border bg-border'
const identityCell = 'bg-card px-[12px] py-[10px]'
const identityDt = 'text-[11px] text-[color:var(--text-2)] lg:text-label'
const identityDd = 'mt-[4px] text-[12px] text-foreground [overflow-wrap:anywhere] lg:text-detail'
const identityNumDd = `${num} ${identityDd}`
const detailHead = 'flex flex-wrap items-start justify-between gap-[12px] max-sm:block'
const actionsRow = 'flex flex-wrap items-center gap-[12px] max-sm:mt-[12px]'

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
      <Link className={`${back} lg:text-label`} to="/carteira">
        <span aria-hidden="true">‹</span> Carteira
      </Link>

      <div className={detailHead}>
        <div>
          <h1 className={`${screenTitle} lg:text-screen-title lg:leading-title`}>{data.underlying}</h1>
          <p className={`${quietLine} lg:text-label`}>
            {positionKindLabel(data.kind)} · {positionStatusLabel(data.status)} · sessão{' '}
            {position.data.as_of_session ?? '—'}
          </p>
        </div>
      </div>

      {writeNotice && (
        <p className={`${notice} lg:text-label`} role="status">
          {writeNotice}
        </p>
      )}
      {(position.data.meta?.truncated || position.data.meta?.note) && (
        <p className={`${notice} lg:text-label`} role="status">
          {position.data.meta.note ??
            'O payoff foi truncado no transporte e não é exibido como completo. Reabra a posição para tentar novamente.'}
        </p>
      )}

      <p className="mb-[16px] text-[11.5px] lg:text-label">
        <span className={`${quiet} lg:text-label`}>Flags da posição</span>{' '}
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

      <section className="mt-[24px]" aria-labelledby="position-identity-title">
        <h2 className={`${blockTitle} lg:text-block-title lg:leading-title`} id="position-identity-title">Registro</h2>
        <dl className={identityGrid}>
          <div className={identityCell}>
            <dt className={identityDt}>Capital comprometido</dt>
            <dd className={identityNumDd}>{brl(data.capital_committed_brl)}</dd>
          </div>
          <div className={identityCell}>
            <dt className={identityDt}>Limite de capital</dt>
            <dd className={identityNumDd}>{brl(data.capital_limit_brl)}</dd>
          </div>
          <div className={identityCell}>
            <dt className={identityDt}>Aberta em</dt>
            <dd className={identityNumDd}>{dateBr(data.opened_session)}</dd>
          </div>
          <div className={identityCell}>
            <dt className={identityDt}>Fechada em</dt>
            <dd className={identityNumDd}>{dateBr(data.closed_session)}</dd>
          </div>
          <div className={identityCell}>
            <dt className={identityDt}>Vencimento</dt>
            <dd className={identityNumDd}>{dateBr(data.maturity)}</dd>
          </div>
          <div className={identityCell}>
            <dt className={identityDt}>Status</dt>
            <dd className={identityDd}>{positionStatusLabel(data.status)}</dd>
          </div>
        </dl>
        {data.notes && <p className="mt-[12px] text-[13px] leading-[1.55] [overflow-wrap:anywhere] whitespace-pre-wrap text-[color:var(--text-2)] lg:text-body lg:leading-body">{data.notes}</p>}
      </section>

      <section className="mt-[24px]" aria-labelledby="position-legs-title">
        <h2 className={`${blockTitle} lg:text-block-title lg:leading-title`} id="position-legs-title">
          Pernas registradas
          <HelpTip
            label="O que é uma perna?"
            text="Perna é cada operação que compõe a posição: uma ação ou uma opção, com lado (comprada/vendida) e quantidade. Uma coberta, por exemplo, tem duas pernas: a ação comprada e a call vendida."
          />
        </h2>
        <ScreenTable
          wrapperClassName={`table-wrap ${focusInset}`}
          wrapperProps={{
            role: 'region',
            tabIndex: 0,
            'aria-label': 'Tabela horizontal das pernas registradas',
          }}
          className="min-w-[760px]"
        >
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
        </ScreenTable>
      </section>

      <section className="mt-[24px]" aria-labelledby="position-marks-title">
        <h2 className={`${blockTitle} lg:text-block-title lg:leading-title`} id="position-marks-title">Marcação da sessão</h2>
        <ScreenTable
          wrapperClassName={`table-wrap ${focusInset}`}
          wrapperProps={{
            role: 'region',
            tabIndex: 0,
            'aria-label': 'Tabela horizontal da marcação da sessão',
          }}
          className="min-w-[980px]"
        >
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
                  <TableCell className="min-w-[160px] px-2 py-2.5">
                    <PositionFlags flags={mark.flags} emptyLabel="—" />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
        </ScreenTable>
      </section>

      <section className="mt-[24px]" aria-labelledby="position-thesis-title">
        <h2 className={`${blockTitle} lg:text-block-title lg:leading-title`} id="position-thesis-title">Tese vs realidade</h2>
        <div className="rounded-[6px] border border-border bg-card p-[16px]">
          <p className="mb-[16px] text-[14px] leading-[1.55] text-foreground [overflow-wrap:anywhere] lg:text-body lg:leading-body">
            {data.thesis_line ?? 'Nenhuma linha de tese retornada para esta posição.'}
          </p>
          {data.prediction ? (
            <dl className={identityGrid}>
              <div className={identityCell}>
                <dt className={identityDt}>Métrica</dt>
                <dd className={identityNumDd}>{data.prediction.metric}</dd>
              </div>
              <div className={identityCell}>
                <dt className={identityDt}>Condição</dt>
                <dd className={identityNumDd}>
                  {data.prediction.operator} {fmt(data.prediction.value)}
                </dd>
              </div>
              <div className={identityCell}>
                <dt className={identityDt}>Horizonte</dt>
                <dd className={identityNumDd}>{fmt(data.prediction.horizon_sessions, 0)} sessões</dd>
              </div>
              <div className={identityCell}>
                <dt className={identityDt}>Avaliar em</dt>
                <dd className={identityNumDd}>{dateBr(data.prediction.evaluate_on)}</dd>
              </div>
              <div className={identityCell}>
                <dt className={identityDt}>Status</dt>
                <dd className={identityDd}>{predictionStatusLabel(data.prediction.status)}</dd>
              </div>
              <div className={identityCell}>
                <dt className={identityDt}>Observado</dt>
                <dd className={identityNumDd}>{fmt(data.prediction.observed)}</dd>
              </div>
            </dl>
          ) : (
            <p className={`${note} lg:text-label`}>Sem previsão estruturada; esta posição não entra na calibração.</p>
          )}
        </div>
      </section>

      <section className="mt-[24px]" aria-labelledby="position-payoff-title">
        <h2 className={`${blockTitle} lg:text-block-title lg:leading-title`} id="position-payoff-title">Payoff no vencimento</h2>
        <p className={`${chainNote} lg:text-label lg:leading-body`}>Preços de entrada do journal; o backend calcula todos os pontos.</p>
        {!payoffTruncated && (
          <PayoffChart
            points={data.payoff}
            breakevens={data.breakeven_brl}
            ariaLabel={`Payoff no vencimento da posição de ${data.underlying}`}
          />
        )}
      </section>

      {isOpen && (
        <section className="mt-[24px] border-t border-t-border pt-[16px]" aria-labelledby="position-close-title">
          <h2 className={`${blockTitle} lg:text-block-title lg:leading-title`} id="position-close-title">Fechamento</h2>
          {!auth.hasToken && !confirmOpen && (
            <TokenGate compact notice={tokenNotice} onUnlock={() => setTokenNotice(null)} />
          )}
          <div className={actionsRow}>
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
            <label className={field}>
              <span className={`${fieldLabel} lg:text-label`}>Data da sessão (opcional)</span>
              <Input
                type="date"
                className="min-h-[44px] font-mono text-[12px] text-foreground tabular-nums focus-visible:outline-2 focus-visible:outline-primary focus-visible:outline-offset-[-2px]"
                value={closeDate}
                onChange={(event) => setCloseDate(event.target.value)}
                data-autofocus
                disabled={close.isPending}
              />
            </label>
            <p className={`${note} lg:text-label`}>Em branco, o backend usa a última sessão disponível.</p>
            {close.isError && <ErrorState error={close.error} title="Fechamento não registrado" />}
            <div className="mt-[4px] flex flex-wrap justify-end gap-[8px] max-sm:grid max-sm:grid-cols-[minmax(0,1fr)]">
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
