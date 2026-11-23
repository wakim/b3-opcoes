import { createFileRoute } from '@tanstack/react-router'

export const Route = createFileRoute('/aprender')({
  component: () => <h1 className="screen-title">Aprender</h1>,
})
