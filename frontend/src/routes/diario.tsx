import { createFileRoute } from '@tanstack/react-router'

export const Route = createFileRoute('/diario')({
  component: () => <h1 className="screen-title">Diário</h1>,
})
