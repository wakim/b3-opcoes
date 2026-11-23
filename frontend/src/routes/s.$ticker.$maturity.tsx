import { createFileRoute } from '@tanstack/react-router'

export const Route = createFileRoute('/s/$ticker/$maturity')({
  component: SeriesPage,
})

function SeriesPage() {
  const { ticker, maturity } = Route.useParams()
  return (
    <h1 className="screen-title">
      {ticker} · {maturity}
    </h1>
  )
}
