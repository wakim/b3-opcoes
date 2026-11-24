import { createFileRoute } from '@tanstack/react-router'

export const Route = createFileRoute('/diario')({
  component: Diario,
})

function Diario() {
  return (
    <>
      <h1 className="screen-title">Diário</h1>
      <div className="placeholder">
        A Carteira já registra posições e previsões vinculadas.
        <br />
        Decisões sem posição e calibração pessoal entram no F7.
      </div>
    </>
  )
}
