import { createFileRoute } from '@tanstack/react-router'

export const Route = createFileRoute('/diario')({
  component: Diario,
})

function Diario() {
  return (
    <>
      <h1 className="screen-title">Diário</h1>
      <div className="placeholder">
        Decisões e previsões estruturadas entram no F6/F7.
        <br />
        Nenhuma rota de escrita existe nesta versão.
      </div>
    </>
  )
}
