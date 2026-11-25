import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link, createFileRoute } from '@tanstack/react-router'
import { fetchScreenAll, type ScreenRow } from '../lib/api'
import { brl, dateBr, fmt, sharePct } from '../lib/format'
import { tickerLink } from '../lib/classes'
import { ErrorState, Loading } from '../components/State'
import { GlossaryMetric, Metric, MetricGrid } from '../components/Metric'
import { MetricModal } from '../components/MetricModal'
import { TickerSearch } from '../components/TickerSearch'
import { LiquidityPanel } from '../components/LiquidityPanel'
import { InfoTip } from '../components/InfoTip'
import { ScreenTable } from '../components/ScreenTable'
import { Button } from '../components/ui/button'
import {
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '../components/ui/table'
import type { ModalTarget, OpenMetric } from '../components/metricKeys'

export const Route = createFileRoute('/')({
  component: Hoje,
})

const headClass = 'px-[8px] py-[10px] font-normal text-[color:var(--text-2)] lg:text-table-head'

function PapersTable({ rows, withOptions }: { rows: ScreenRow[]; withOptions: boolean }) {
  if (rows.length === 0) {
    return <p className="my-[8px] text-[calc(11.5px*var(--font-scale))] text-muted-foreground lg:text-detail">nenhum papel nesta seção do recorte</p>
  }
  return (
    <ScreenTable
      wrapperClassName="table-wrap table-wrap--scroll min-w-0 max-w-full"
      wrapperProps={{
        role: 'region',
        'aria-label': withOptions ? 'Papéis com opções' : 'Papéis sem opções',
        tabIndex: 0,
      }}
    >
      <TableHeader>
          <TableRow>
            <TableHead scope="col" className={headClass}>Papel</TableHead>
            <TableHead scope="col" className={`text-right ${headClass}`}>Fechamento</TableHead>
            {withOptions && (
              <>
                <TableHead scope="col" className={`text-right ${headClass}`}>Volatilidade implícita 30d</TableHead>
                <InfoTip text="VRP = volatilidade implícita − realizada">
                  <TableHead scope="col" className={`text-right ${headClass}`} aria-label="Prêmio de volatilidade. VRP = volatilidade implícita − realizada">Prêmio de volatilidade</TableHead>
                </InfoTip>
                <TableHead scope="col" className={`text-right ${headClass}`}>Séries líquidas</TableHead>
                <TableHead scope="col" className={`text-right ${headClass}`}>Vencimentos</TableHead>
              </>
            )}
            <TableHead scope="col" className={`text-right ${headClass}`}>Situação</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row) => (
            <TableRow key={row.underlying}>
              <TableCell className="px-[8px] py-[10px]">
                {row.status === 'ok' ? (
                  <Link className={`${tickerLink} lg:text-table`} to="/t/$ticker" params={{ ticker: row.underlying }}>
                    {row.underlying}
                  </Link>
                ) : (
                  row.underlying
                )}
              </TableCell>
              <TableCell className="px-[8px] py-[10px]">{brl(row.spot_brl)}</TableCell>
              {withOptions && (
                <>
                  <TableCell className="px-[8px] py-[10px]">{fmt(row.iv_atm_30d)}</TableCell>
                  {row.vrp != null && row.flags.includes('rv_shock_day') ? (
                    <InfoTip text={`${sharePct(row.rv_shock_share_pct)} da variância de 30 pregões veio de ${dateBr(row.rv_shock_date)}`}>
                      <TableCell className="px-[8px] py-[10px]">
                        {fmt(row.vrp)}
                        <span
                          className="ml-[3px] cursor-help text-muted-foreground"
                          aria-label={`realizada dominada por ${dateBr(row.rv_shock_date)}`}
                        >
                          †
                        </span>
                      </TableCell>
                    </InfoTip>
                  ) : (
                    <TableCell className="px-[8px] py-[10px]">{fmt(row.vrp)}</TableCell>
                  )}
                  <TableCell className="px-[8px] py-[10px]">{row.n_liquid_series}</TableCell>
                  <TableCell className="px-[8px] py-[10px]">{row.n_expiries}</TableCell>
                </>
              )}
              <TableCell className="text-[color:var(--text-3)] px-[8px] py-[10px]">{row.exclusion ?? 'ok'}</TableCell>
            </TableRow>
          ))}
        </TableBody>
    </ScreenTable>
  )
}

function Hoje() {
  const [modal, setModal] = useState<ModalTarget | null>(null)
  const openMetric: OpenMetric = (field, label, value) => setModal({ field, label, value })
  const screen = useQuery({
    queryKey: ['screen', 'all'],
    queryFn: ({ signal }) => fetchScreenAll(signal),
  })

  if (screen.isError) return <ErrorState error={screen.error} />
  if (screen.isLoading || !screen.data) return <Loading />

  const { summary, rows } = screen.data.data
  const withOptions = rows.filter((row) => row.n_expiries > 0)
  const withoutOptions = rows.filter((row) => row.n_expiries === 0)

  return (
    <>
      <h1 className="mb-[16px] mt-[4px] text-[calc(28px*var(--font-scale))] font-medium leading-normal tracking-[-0.02em] lg:text-screen-title lg:leading-title">Hoje</h1>
      <p className="mb-[16px] text-[calc(12.5px*var(--font-scale))] text-[color:var(--text-2)] lg:text-lede lg:leading-body">Leitura descritiva da sessão. Nenhum número aqui é previsão.</p>

      <TickerSearch />

      <MetricGrid>
        <Metric label="Universo" value={summary.total} />
        <Metric label="Com métrica" value={summary.ok} emphasis />
        <Metric label="Excluídos" value={summary.excluded} />
        <GlossaryMetric
          field="iv_atm_30d"
          label="Volatilidade implícita mediana"
          hint="Volatilidade implícita no dinheiro (ATM), padronizada para 30 dias."
          value={fmt(summary.iv_median)}
          onOpen={openMetric}
        />
        <GlossaryMetric
          field="vrp"
          label="Prêmio de volatilidade mediano"
          hint="VRP = volatilidade implícita − realizada."
          value={fmt(summary.vrp_median)}
          onOpen={openMetric}
        />
      </MetricGrid>

      {summary.rv_shock_count > 0 && (
        <p className="mb-[16px] border-l-2 border-input pl-[10px] text-[calc(11.5px*var(--font-scale))] text-[color:var(--text-2)] lg:text-label" role="status">
          Em {summary.rv_shock_count} de {summary.ok} papéis com métrica, um único pregão responde
          por metade ou mais da oscilação dos últimos 30 pregões
          {summary.rv_shock_dates.length > 0 && (
            <> ({summary.rv_shock_dates.map((item) => `${dateBr(item.date)} em ${item.count}`).join(', ')})</>
          )}
          . Nesses papéis (marcados com †) a volatilidade realizada e o prêmio de volatilidade
          refletem esse dia: o número é real, mas não descreve o regime típico.{' '}
          <Button
            type="button"
            variant="link"
            size="sm"
            className="h-[44px] justify-start rounded-none px-0 font-normal whitespace-normal text-foreground underline underline-offset-2 focus-visible:ring-0 focus-visible:outline-2 focus-visible:outline-solid focus-visible:outline-offset-[-2px] focus-visible:outline-primary lg:text-label"
            onClick={() => openMetric('rv_shock_share_pct', 'Pregão dominante na RV', `${summary.rv_shock_count} papéis`)}
          >
            O que é isso
          </Button>
        </p>
      )}

      <LiquidityPanel rows={rows} />

      <section className="min-w-0 max-w-full">
        <h2 className="mb-[8px] mt-[24px] text-[calc(13px*var(--font-scale))] font-medium lg:text-block-title lg:leading-title">
          Com opções <span className="ml-[8px] font-mono text-[calc(11px*var(--font-scale))] font-normal text-muted-foreground lg:text-label">{withOptions.length} papéis</span>
        </h2>
        <PapersTable rows={withOptions} withOptions />
      </section>

      <section className="mt-[8px] min-w-0 max-w-full">
        <h2 className="mb-[8px] mt-[24px] text-[calc(13px*var(--font-scale))] font-medium lg:text-block-title lg:leading-title">
          Sem opções <span className="ml-[8px] font-mono text-[calc(11px*var(--font-scale))] font-normal text-muted-foreground lg:text-label">{withoutOptions.length} papéis</span>
        </h2>
        <PapersTable rows={withoutOptions} withOptions={false} />
      </section>

      <MetricModal target={modal} onClose={() => setModal(null)} />
    </>
  )
}
