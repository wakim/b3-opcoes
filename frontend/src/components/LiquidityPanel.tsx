import { useMemo, useState, type KeyboardEvent } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { apiGet, type LiquidData, type ScreenRow } from '../lib/api'
import { brl, brlCompact, dateBr, fmt, sharePct } from '../lib/format'
import { ErrorState, Loading } from './State'
import { InfoTip } from './InfoTip'
import { ScreenTable } from './ScreenTable'
import { Button } from './ui/button'
import { ToggleGroup, ToggleGroupItem } from './ui/toggle-group'
import { usePresentation } from '../lib/presentation'
import {
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from './ui/table'

function spreadLabel(spreadPct: number | null, spreadBrl: number | null): string {
  return spreadPct != null ? `${fmt(spreadPct, 1)}%` : brl(spreadBrl)
}

const headClass = 'px-[8px] py-[10px] font-normal text-[color:var(--text-2)] lg:text-table-head'
const tabButtonClass =
  'h-[44px] rounded-[4px] border border-border bg-card px-[12px] py-[8px] font-mono text-[calc(11.5px*var(--font-scale))] font-normal tabular-nums whitespace-normal text-[color:var(--text-2)] shadow-none transition-colors dark:border-border dark:bg-card focus-visible:ring-0 focus-visible:outline-2 focus-visible:outline-solid focus-visible:outline-offset-[-2px] focus-visible:outline-primary lg:text-control data-[state=on]:border-input data-[state=on]:bg-muted data-[state=on]:text-foreground dark:data-[state=on]:border-input dark:data-[state=on]:bg-muted'

/** Descoberta descritiva: liquidez do papel e séries mais negociadas da sessão. */
export function LiquidityPanel({ rows }: { rows: ScreenRow[] }) {
  const p = usePresentation()
  const [tab, setTab] = useState<'papers' | 'series'>('papers')
  const [showAll, setShowAll] = useState(false)
  const navigate = useNavigate()

  const ranked = useMemo(
    () =>
      rows
        .filter((row) => row.status === 'ok' && row.n_liquid_series > 0)
        .sort(
          (a, b) =>
            b.n_liquid_series - a.n_liquid_series ||
            b.n_expiries - a.n_expiries ||
            b.n_series - a.n_series ||
            a.underlying.localeCompare(b.underlying),
        ),
    [rows],
  )
  const visiblePapers = showAll ? ranked : ranked.slice(0, 10)

  const liquid = useQuery({
    queryKey: ['liquid', 'top-volume'],
    queryFn: ({ signal }) => apiGet<LiquidData>('/api/liquid?limit=20', signal),
    enabled: tab === 'series',
    staleTime: 60_000,
  })

  const openTicker = (ticker: string) => {
    void navigate({ to: '/t/$ticker', params: { ticker } })
  }
  const openSeries = (ticker: string, maturity: string) => {
    void navigate({ to: '/s/$ticker/$maturity', params: { ticker, maturity } })
  }
  const rowKeyDown = (action: () => void) => (event: KeyboardEvent<HTMLTableRowElement>) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()
      action()
    }
  }

  return (
    <section className="mt-[24px] min-w-0 max-w-full" aria-labelledby="liquidity-title">
      <div className="mt-0 flex min-w-0 max-w-full flex-wrap items-center justify-between gap-[8px]">
        <h2 className="mb-[8px] text-[calc(13px*var(--font-scale))] font-medium lg:text-block-title lg:leading-title" id="liquidity-title">
          Liquidez <span className="ml-[8px] font-mono text-[calc(11px*var(--font-scale))] font-normal text-muted-foreground lg:text-label">{ranked.length} papéis com série líquida</span>
        </h2>
        <ToggleGroup
          type="multiple"
          value={[tab]}
          onValueChange={(values) => { if (values.length) setTab(values[values.length - 1] as 'papers' | 'series') }}
          className="flex max-w-full flex-wrap gap-[4px]"
          aria-label="Tipo de liquidez"
        >
          <ToggleGroupItem value="papers" variant="outline" className={tabButtonClass}>
            Papéis
          </ToggleGroupItem>
          <ToggleGroupItem value="series" variant="outline" className={tabButtonClass}>
            Séries mais negociadas
          </ToggleGroupItem>
        </ToggleGroup>
      </div>
      <p className="mb-[8px] text-[calc(11px*var(--font-scale))] leading-[1.45] text-muted-foreground lg:text-label lg:leading-body">{p.copy('liquidity', 'order_note').label}</p>

      {tab === 'papers' ? (
        ranked.length === 0 ? (
          <p className="my-[8px] text-[calc(11.5px*var(--font-scale))] text-muted-foreground lg:text-detail">nenhum papel com série líquida nesta sessão</p>
        ) : (
          <>
            <ScreenTable
              wrapperClassName="table-wrap table-wrap--scroll min-w-0 max-w-full"
              wrapperProps={{
                id: 'liquidity-papers',
                role: 'region',
                'aria-label': 'Papéis com mais séries líquidas',
                tabIndex: 0,
              }}
              className="liquidity-table"
            >
              <TableHeader>
                  <TableRow>
                    <TableHead scope="col" className={headClass}>Papel</TableHead>
                    <TableHead scope="col" className={`text-right ${headClass}`}>Fechamento</TableHead>
                    <TableHead scope="col" className={`text-right ${headClass}`}>Séries líquidas</TableHead>
                    <TableHead scope="col" className={`text-right ${headClass}`}>Vencimentos</TableHead>
                    <TableHead scope="col" className={`text-right ${headClass}`}>Volatilidade implícita 30d</TableHead>
                    <InfoTip term="vrp">
                      <TableHead scope="col" className={`text-right ${headClass}`} aria-label={`${p.copy('vrp').label}. ${p.copy('vrp').help ?? ''}`}>Prêmio de volatilidade</TableHead>
                    </InfoTip>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {visiblePapers.map((row) => (
                    <TableRow
                      key={row.underlying}
                      role="link"
                      tabIndex={0}
                      aria-label={`Abrir ${row.underlying}`}
                      onClick={() => openTicker(row.underlying)}
                      onKeyDown={rowKeyDown(() => openTicker(row.underlying))}
                    >
                      <TableCell className="font-mono tabular-nums px-[8px] py-[12px] leading-[calc(20px*var(--font-scale))] lg:leading-table">{row.underlying}</TableCell>
                      <TableCell className="px-[8px] py-[12px] leading-[calc(20px*var(--font-scale))] lg:leading-table">{brl(row.spot_brl)}</TableCell>
                      <TableCell className="px-[8px] py-[12px] leading-[calc(20px*var(--font-scale))] lg:leading-table">{row.n_liquid_series}</TableCell>
                      <TableCell className="px-[8px] py-[12px] leading-[calc(20px*var(--font-scale))] lg:leading-table">{row.n_expiries}</TableCell>
                      <TableCell className="px-[8px] py-[12px] leading-[calc(20px*var(--font-scale))] lg:leading-table">{fmt(row.iv_atm_30d)}</TableCell>
                      {row.vrp != null && row.flags.includes('rv_shock_day') ? (
                        <InfoTip text={p.copy('rv_shock', 'help', { share: sharePct(row.rv_shock_share_pct), date: dateBr(row.rv_shock_date) }).label}>
                          <TableCell className="px-[8px] py-[12px] leading-[calc(20px*var(--font-scale))] lg:leading-table">
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
                        <TableCell className="px-[8px] py-[12px] leading-[calc(20px*var(--font-scale))] lg:leading-table">{fmt(row.vrp)}</TableCell>
                      )}
                    </TableRow>
                  ))}
                </TableBody>
            </ScreenTable>
            {ranked.length > 10 && (
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="mt-[8px] h-[44px] max-sm:w-full rounded-[4px] border-border bg-card px-[12px] py-[8px] font-normal whitespace-normal text-[calc(11.5px*var(--font-scale))] text-[color:var(--text-2)] shadow-none transition-colors hover:bg-muted hover:text-foreground dark:border-border dark:bg-card dark:hover:bg-muted focus-visible:ring-0 focus-visible:outline-2 focus-visible:outline-solid focus-visible:outline-offset-[-2px] focus-visible:outline-primary lg:text-control"
                aria-expanded={showAll}
                aria-controls="liquidity-papers"
                onClick={() => setShowAll((value) => !value)}
              >
                {showAll ? 'Mostrar top 10' : `Ver todos os ${ranked.length}`}
              </Button>
            )}
          </>
        )
      ) : liquid.isLoading ? (
        <Loading label={p.copy('loading', 'series').label} />
      ) : liquid.isError ? (
        <ErrorState error={liquid.error} />
      ) : !liquid.data ? (
        <Loading label={p.copy('loading', 'series').label} />
      ) : liquid.data.data.rows.length === 0 ? (
        <p className="my-[8px] text-[calc(11.5px*var(--font-scale))] text-muted-foreground lg:text-detail">nenhuma série líquida nesta sessão</p>
      ) : (
        <>
          {liquid.data.meta?.truncated && (
            <p className="mb-[16px] border-l-2 border-input pl-[10px] text-[calc(11.5px*var(--font-scale))] text-[color:var(--text-2)] lg:text-label" role="status">
              Mostrando {liquid.data.meta.returned} de {liquid.data.meta.total} séries por volume.
            </p>
          )}
          <ScreenTable
            wrapperClassName="table-wrap table-wrap--scroll min-w-0 max-w-full"
            wrapperProps={{
              role: 'region',
              'aria-label': 'Séries líquidas mais negociadas',
              tabIndex: 0,
            }}
            className="liquidity-table liquidity-table--series"
          >
            <TableHeader>
                <TableRow>
                  <TableHead scope="col" className={headClass}>Série</TableHead>
                  <TableHead scope="col" className={`text-right ${headClass}`}>Papel</TableHead>
                  <TableHead scope="col" className={`text-right ${headClass}`}>Lado / estilo</TableHead>
                  <TableHead scope="col" className={`text-right ${headClass}`}>Strike</TableHead>
                  <TableHead scope="col" className={`text-right ${headClass}`}>Vencimento</TableHead>
                  <InfoTip term="volume">
                    <TableHead scope="col" className={`text-right ${headClass}`} aria-label="Volume. Volume financeiro da sessão, em reais">Volume</TableHead>
                  </InfoTip>
                  <TableHead scope="col" className={`text-right ${headClass}`}>Negócios</TableHead>
                  <InfoTip term="spread">
                    <TableHead scope="col" className={`text-right ${headClass}`} aria-label="Spread. Spread percentual do fechamento; reais quando não há percentual">Spread</TableHead>
                  </InfoTip>
                  <TableHead scope="col" className={`text-right ${headClass}`}>Volatilidade implícita</TableHead>
                  <TableHead scope="col" className={`text-right ${headClass}`}>Delta</TableHead>
                  <TableHead scope="col" className={`text-right ${headClass}`}>Liquidez</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {liquid.data.data.rows.map((row) => (
                  <TableRow
                    key={`${row.ticker}:${row.maturity}`}
                    role="link"
                    tabIndex={0}
                    aria-label={`Abrir ${row.ticker}, vencimento ${row.maturity}`}
                    onClick={() => openSeries(row.ticker, row.maturity)}
                    onKeyDown={rowKeyDown(() => openSeries(row.ticker, row.maturity))}
                  >
                    <TableCell className="font-mono tabular-nums px-[8px] py-[12px] leading-[calc(20px*var(--font-scale))] lg:leading-table">{row.ticker}</TableCell>
                    <TableCell className="font-mono tabular-nums px-[8px] py-[12px] leading-[calc(20px*var(--font-scale))] lg:leading-table">{row.underlying}</TableCell>
                    <TableCell className="px-[8px] py-[12px] leading-[calc(20px*var(--font-scale))] lg:leading-table">{p.side(row.side)} / {row.style ? p.style(row.style) : p.copy('option_style', 'unknown').label}</TableCell>
                    <TableCell className="px-[8px] py-[12px] leading-[calc(20px*var(--font-scale))] lg:leading-table">{brl(row.strike_brl)}</TableCell>
                    <TableCell className="font-mono tabular-nums px-[8px] py-[12px] leading-[calc(20px*var(--font-scale))] lg:leading-table">{dateBr(row.maturity)}</TableCell>
                    <InfoTip text={`Volume financeiro: ${brl(row.volume_brl)}`}>
                      <TableCell className="px-[8px] py-[12px] leading-[calc(20px*var(--font-scale))] lg:leading-table" aria-label={`Volume financeiro: ${brl(row.volume_brl)}`}>{brlCompact(row.volume_brl)}</TableCell>
                    </InfoTip>
                    <TableCell className="px-[8px] py-[12px] leading-[calc(20px*var(--font-scale))] lg:leading-table">{row.trades}</TableCell>
                    <TableCell className="px-[8px] py-[12px] leading-[calc(20px*var(--font-scale))] lg:leading-table">{spreadLabel(row.spread_pct, row.spread_brl)}</TableCell>
                    <TableCell className="px-[8px] py-[12px] leading-[calc(20px*var(--font-scale))] lg:leading-table">{fmt(row.iv_pct)}</TableCell>
                    <TableCell className="px-[8px] py-[12px] leading-[calc(20px*var(--font-scale))] lg:leading-table">{fmt(row.delta, 3)}</TableCell>
                    <TableCell className="text-[color:var(--text-3)] px-[8px] py-[12px] leading-[calc(20px*var(--font-scale))] lg:leading-table">{row.is_liquid ? 'líquida' : '—'}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
          </ScreenTable>
        </>
      )}
    </section>
  )
}
