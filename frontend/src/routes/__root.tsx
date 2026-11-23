import { Link, Outlet, createRootRoute } from '@tanstack/react-router'

// O provider do TanStack Query e o carimbo vêm aqui (F5.4/F5.3 preenchem o
// carimbo a partir de /api/health). Nesta rodada de scaffold é só a casca.
export const Route = createRootRoute({
  component: RootLayout,
})

function RootLayout() {
  return (
    <div className="app">
      <header className="appbar">
        <h1>Opções B3</h1>
        <div className="stamp">carregando sessão…</div>
      </header>
      <main>
        <Outlet />
      </main>
      <nav className="nav">
        <Link to="/" className="nav-link">Hoje</Link>
        <Link to="/carteira" className="nav-link">Carteira</Link>
        <Link to="/diario" className="nav-link">Diário</Link>
        <Link to="/aprender" className="nav-link">Aprender</Link>
      </nav>
    </div>
  )
}
