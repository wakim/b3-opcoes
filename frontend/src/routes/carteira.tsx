import { createFileRoute } from '@tanstack/react-router'

export const Route = createFileRoute('/carteira')({
  component: () => <h1 className="screen-title">Carteira</h1>,
})
