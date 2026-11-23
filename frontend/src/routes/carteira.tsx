import { createFileRoute } from '@tanstack/react-router'

export const Route = createFileRoute('/carteira')({
  component: Carteira,
})

function Carteira() {
  return (
    <>
      <h1 className="screen-title">Carteira</h1>
      <div className="placeholder">
        Posições e marcação EOD entram no F6.
        <br />
        Nenhuma rota de escrita existe nesta versão.
      </div>
    </>
  )
}
