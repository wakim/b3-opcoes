/** Busca de papel (B1): input no topo de Hoje, resultado quieto.
 *
 * O servidor faz o filtro (prefixo primeiro, depois contém) e capa em 60 com
 * `meta.total` honesto. `q` vazio não dispara chamada. Cada linha é um link
 * com alvo ≥ 44 px — teclado navega por Tab, sem depender de hover.
 */
import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { apiGet, type TickerData } from '../lib/api'
import { ErrorState } from './State'
import { usePresentation } from '../lib/presentation'
import { Input } from './ui/input'

export function TickerSearch() {
  const p = usePresentation()
  const [query, setQuery] = useState('')
  const [debounced, setDebounced] = useState('')

  useEffect(() => {
    const timer = setTimeout(() => setDebounced(query.trim()), 200)
    return () => clearTimeout(timer)
  }, [query])

  const search = useQuery({
    queryKey: ['tickers', debounced],
    queryFn: ({ signal }) =>
      apiGet<TickerData>(`/api/tickers?q=${encodeURIComponent(debounced)}`, signal),
    enabled: debounced.length > 0,
    staleTime: 3_600_000,
  })

  const rows = search.data?.data.rows ?? []
  const total = search.data?.meta?.total ?? rows.length

  return (
    <div className="mb-[16px] min-w-0 max-w-full">
      <label className="mb-[4px] block text-[calc(11px*var(--font-scale))] text-[color:var(--text-2)] lg:text-label" htmlFor="ticker-search">
        {p.copy('search', 'label').label}
      </label>
      <Input
        id="ticker-search"
        className="rounded-[4px] border-border bg-card px-[12px] py-[10px] text-[calc(14px*var(--font-scale))] shadow-none dark:bg-card focus-visible:ring-0 focus-visible:outline-2 focus-visible:outline-solid focus-visible:outline-offset-[-2px] focus-visible:outline-primary lg:text-search"
        type="search"
        value={query}
        onChange={(event) => setQuery(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Escape') setQuery('')
        }}
        placeholder={p.copy('search', 'placeholder').label}
        autoComplete="off"
        spellCheck={false}
        aria-label={p.copy('search', 'aria').label}
      />

      {debounced.length > 0 && (
        <div className="mt-[6px]" aria-live="polite">
          {search.isLoading && <p className="my-[8px] text-[calc(11px*var(--font-scale))] text-muted-foreground lg:text-label">{p.copy('loading', 'search').label}</p>}
          {search.isError && <ErrorState error={search.error} title="Busca indisponível" />}
          {search.data && rows.length === 0 && (
            <p className="my-[8px] text-[calc(11px*var(--font-scale))] text-muted-foreground lg:text-label">{p.copy('search', 'empty', { q: debounced }).label}</p>
          )}
          {search.data && rows.length > 0 && (
            <>
              <p className="my-[8px] text-[calc(11px*var(--font-scale))] text-muted-foreground lg:text-label">
                {rows.length < total
                  ? p.copy('search', 'showing', { returned: rows.length, total }).label
                  : p.copy(total === 1 ? 'search' : 'search', total === 1 ? 'total_one' : 'total_many', { total }).label}
              </p>
              <ul className="mb-0 mt-[4px] list-none overflow-hidden rounded-[6px] border border-border p-0">
                {rows.map((row, index) => (
                  <li key={row.ticker} className={index > 0 ? 'border-t border-border' : undefined}>
                    <Link
                      className="flex min-h-[44px] flex-wrap items-baseline gap-x-[10px] gap-y-[2px] bg-card px-[12px] py-[10px] transition-colors hover:bg-muted focus-visible:-outline-offset-2 focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
                      to="/t/$ticker"
                      params={{ ticker: row.ticker }}
                    >
                      <span className="font-mono font-medium tabular-nums text-primary lg:text-search">{row.ticker}</span>
                      <span className="min-w-0 flex-[1_1_150px] overflow-hidden text-ellipsis whitespace-nowrap text-[calc(11.5px*var(--font-scale))] text-[color:var(--text-2)] lg:text-search">{row.corp_name}</span>
                      <span className="basis-full text-[calc(10.5px*var(--font-scale))] text-muted-foreground lg:text-search-meta">
                        {p.copy(row.in_universe ? 'search' : 'search', row.in_universe ? 'in_universe' : 'out_universe').label} ·{' '}
                        {p.copy('search', row.has_options ? 'with_options' : 'without_options').label}
                      </span>
                    </Link>
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      )}
    </div>
  )
}
