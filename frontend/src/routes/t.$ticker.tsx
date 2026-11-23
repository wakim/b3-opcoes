import { createFileRoute } from '@tanstack/react-router'

export const Route = createFileRoute('/t/$ticker')({
  component: TickerPage,
})

function TickerPage() {
  const { ticker } = Route.useParams()
  return <h1 className="screen-title">{ticker}</h1>
}
