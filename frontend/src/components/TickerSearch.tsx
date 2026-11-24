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

export function TickerSearch() {
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
    <div className="ticker-search">
      <label className="ticker-search__label lg:text-label" htmlFor="ticker-search">
        Buscar papel
      </label>
      <input
        id="ticker-search"
        className="ticker-search__input lg:text-search"
        type="search"
        value={query}
        onChange={(event) => setQuery(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Escape') setQuery('')
        }}
        placeholder="ticker ou nome (ex.: PETR4)"
        autoComplete="off"
        spellCheck={false}
        aria-label="Buscar papel por ticker ou nome"
      />

      {debounced.length > 0 && (
        <div className="ticker-search__results" aria-live="polite">
          {search.isLoading && <p className="note lg:text-label">buscando…</p>}
          {search.isError && <ErrorState error={search.error} title="Busca indisponível" />}
          {search.data && rows.length === 0 && (
            <p className="note lg:text-label">nenhum papel para “{debounced}”</p>
          )}
          {search.data && rows.length > 0 && (
            <>
              <p className="note lg:text-label">
                {rows.length < total
                  ? `mostrando ${rows.length} de ${total} papéis`
                  : `${total} ${total === 1 ? 'papel' : 'papéis'}`}
              </p>
              <ul className="ticker-search__list">
                {rows.map((row) => (
                  <li key={row.ticker}>
                    <Link className="ticker-search__row" to="/t/$ticker" params={{ ticker: row.ticker }}>
                      <span className="ticker-search__code num lg:text-search">{row.ticker}</span>
                      <span className="ticker-search__name lg:text-search">{row.corp_name}</span>
                      <span className="ticker-search__meta quiet lg:text-search-meta">
                        {row.in_universe ? 'no universo' : 'fora do universo'} ·{' '}
                        {row.has_options ? 'com opções' : 'sem opções'}
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
