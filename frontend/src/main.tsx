import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { RouterProvider, createRouter } from '@tanstack/react-router'
import { registerSW } from 'virtual:pwa-register'
import { routeTree } from './routeTree.gen'
import './styles.css'
import './styles-view.css'

// PWA (F5.7): o service worker guarda o shell e o cache de /api. `autoUpdate`
// troca a versão em segundo plano; o banner offline lê o último /api/health.
registerSW({ immediate: true })

const router = createRouter({ routeTree })

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router
  }
}

const root = document.getElementById('root')
if (!root) throw new Error('elemento #root ausente no index.html')

createRoot(root).render(
  <StrictMode>
    <RouterProvider router={router} />
  </StrictMode>,
)
