import { defineConfig } from 'vite'
import { TanStackRouterVite } from '@tanstack/router-plugin/vite'
import tailwindcss from '@tailwindcss/vite'

// SPA pura: o plugin de rotas file-based gera src/routeTree.gen.ts a partir de
// src/routes/. Sem servidor no frontend, sem biblioteca de gráfico (ADR stack F5).
export default defineConfig({
  plugins: [
    TanStackRouterVite({ target: 'react', autoCodeSplitting: false }),
    tailwindcss(),
  ],
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
})
