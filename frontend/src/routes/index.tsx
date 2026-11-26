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
import { usePresentation } from '../lib/presentation'

export const Route = createFileRoute('/')({
  component: Hoje,
})

const headClass = 'px-[8px] py-[10px] font-normal text-[color:var(--text-2)] lg:text-table-head'

function PapersTable({ rows, withOptions }: { rows: ScreenRow[]; withOptions: boolean }) {
  const p = usePresentation()
  if (rows.length === 0) {
    return <p className="my-[8px] text-[calc(11.5px*var(--font-scale))] text-muted-foreground lg:text-detail">{p.copy('empty', 'papers').label}</p>
  }
  return (
    <ScreenTable
      wrapperClassName="table-wrap table-wrap--scroll min-w-0 max-w-full"
      wrapperProps={{
        role: 'region',
        'aria-label': p.copy('search', withOptions ? 'region_with_options' : 'region_without_options').label,
        tabIndex: 0,
      }}
    >
      <TableHeader>
          <TableRow>
            <TableHead scope="col" className={headClass}>{p.copy('liquidity', 'paper_column').label}</TableHead>
            <TableHead scope="col" className={`text-right ${headClass}`}>{p.copy('spot', 'close').label}</TableHead>
            {withOptions && (
              <>
                <TableHead scope="col" className={`text-right ${headClass}`}>{p.copy('iv_atm_30d').label}</TableHead>
                <InfoTip term="vrp">
                  <TableHead scope="col" className={`text-right ${headClass}`} aria-label={`${p.copy('vrp').label}. ${p.copy('vrp').help ?? ''}`}>{p.copy('vrp').label}</TableHead>
                </InfoTip>
                <TableHead scope="col" className={`text-right ${headClass}`}>{p.copy('liquid_series').label}</TableHead>
                <TableHead scope="col" className={`text-right ${headClass}`}>{p.copy('liquidity', 'expiries_column').label}</TableHead>
              </>
            )}
            <TableHead scope="col" className={`text-right ${headClass}`}>{p.copy('exclusion').label}</TableHead>
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
                    <InfoTip
                      term="rv_shock"
                      variant="help"
                      params={{ share: sharePct(row.rv_shock_share_pct), date: dateBr(row.rv_shock_date) }}
                    >
                      <TableCell className="px-[8px] py-[10px]">
                        {fmt(row.vrp)}
                        <span
                          className="ml-[3px] cursor-help text-muted-foreground"
                          aria-label={p.copy('rv_shock', 'marker', { date: dateBr(row.rv_shock_date) }).label}
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
              <TableCell className="text-[color:var(--text-3)] px-[8px] py-[10px]">{p.exclusion(row.exclusion ?? 'ok').label}</TableCell>
            </TableRow>
          ))}
        </TableBody>
    </ScreenTable>
  )
}

function Hoje() {
  const p = usePresentation()
  const [modal, setModal] = useState<ModalTarget | null>(null)
  const openMetric: OpenMetric = (field, label, value, term, variant, params) =>
    setModal({ field, label, value, term, variant, params })
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
      <h1 className="mb-[16px] mt-[4px] text-[calc(28px*var(--font-scale))] font-medium leading-normal tracking-[-0.02em] lg:text-screen-title lg:leading-title">{p.copy('screen', 'hoje').label}</h1>
      <p className="mb-[16px] text-[calc(12.5px*var(--font-scale))] text-[color:var(--text-2)] lg:text-lede lg:leading-body">{p.copy('descriptive_note').label}</p>

      <TickerSearch />

      <MetricGrid>
        <Metric term="summary" variant="universe" value={summary.total} />
        <Metric term="summary" variant="metric" value={summary.ok} emphasis />
        <Metric term="summary" variant="excluded" value={summary.excluded} />
        <GlossaryMetric
          field="iv_atm_30d"
          term="iv_atm_30d"
          variant="median"
          value={fmt(summary.iv_median)}
          onOpen={openMetric}
        />
        <GlossaryMetric
          field="vrp"
          term="vrp"
          variant="median"
          value={fmt(summary.vrp_median)}
          onOpen={openMetric}
        />
      </MetricGrid>

      {summary.rv_shock_count > 0 && (
        <p className="mb-[16px] border-l-2 border-input pl-[10px] text-[calc(11.5px*var(--font-scale))] text-[color:var(--text-2)] lg:text-label" role="status">
          {p.copy('rv_shock', 'banner', { count: summary.rv_shock_count, total: summary.ok }).label}
          {summary.rv_shock_dates.length > 0 && (
            <> ({summary.rv_shock_dates.map((item) => `${dateBr(item.date)} em ${item.count}`).join(', ')})</>
          )}{' '}
          <Button
            type="button"
            variant="link"
            size="sm"
            className="h-[44px] justify-start rounded-none px-0 font-normal whitespace-normal text-foreground underline underline-offset-2 focus-visible:ring-0 focus-visible:outline-2 focus-visible:outline-solid focus-visible:outline-offset-[-2px] focus-visible:outline-primary lg:text-label"
            onClick={() => openMetric(
              'rv_shock_share_pct',
              p.copy('rv_shock', 'what').label,
              `${summary.rv_shock_count} ${p.copy('action', 'papers').label}`,
              'rv_shock',
              'what',
            )}
          >
            {p.copy('rv_shock', 'what').label}
          </Button>
        </p>
      )}

      <LiquidityPanel rows={rows} />

      <section className="min-w-0 max-w-full">
        <h2 className="mb-[8px] mt-[24px] text-[calc(13px*var(--font-scale))] font-medium lg:text-block-title lg:leading-title">
          {p.copy('search', 'region_with_options').label} <span className="ml-[8px] font-mono text-[calc(11px*var(--font-scale))] font-normal text-muted-foreground lg:text-label">{withOptions.length} {p.copy('action', 'papers').label}</span>
        </h2>
        <PapersTable rows={withOptions} withOptions />
      </section>

      <section className="mt-[8px] min-w-0 max-w-full">
        <h2 className="mb-[8px] mt-[24px] text-[calc(13px*var(--font-scale))] font-medium lg:text-block-title lg:leading-title">
          {p.copy('search', 'region_without_options').label} <span className="ml-[8px] font-mono text-[calc(11px*var(--font-scale))] font-normal text-muted-foreground lg:text-label">{withoutOptions.length} {p.copy('action', 'papers').label}</span>
        </h2>
        <PapersTable rows={withoutOptions} withOptions={false} />
      </section>

      <MetricModal target={modal} onClose={() => setModal(null)} />
    </>
  )
}
