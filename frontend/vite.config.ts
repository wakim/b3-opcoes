import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineConfig, type Plugin } from 'vite'
import { TanStackRouterVite } from '@tanstack/router-plugin/vite'
import tailwindcss from '@tailwindcss/vite'
import { VitePWA } from 'vite-plugin-pwa'

// O bind do Windows não suporta `copy_file_range` (EPERM no copyFileSync do
// Vite ao copiar `public/`). Emitir os arquivos como assets usa writeFileSync e
// funciona; o `publicDir` segue ativo no dev server.
function publicAssets(): Plugin {
  return {
    name: 'b3o-public-assets',
    apply: 'build',
    buildStart() {
      const walk = (dir: string): string[] =>
        readdirSync(dir).flatMap((name) => {
          const full = join(dir, name)
          return statSync(full).isDirectory() ? walk(full) : [full]
        })
      for (const file of walk('public')) {
        this.emitFile({
          type: 'asset',
          fileName: relative('public', file).replaceAll('\\', '/'),
          source: readFileSync(file),
        })
      }
    },
  }
}


// SPA pura: o plugin de rotas file-based gera src/routeTree.gen.ts a partir de
// src/routes/. Sem servidor no frontend, sem biblioteca de gráfico (ADR stack F5).
//
// PWA (F5.7): precache do shell + NetworkFirst em /api. A sonda de rede
// (`/api/health?probe=1`) fica de fora do cache — ela é justamente quem diz se o
// servidor está no ar (cache não pode responder "online" por ele).
export default defineConfig({
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  plugins: [
    publicAssets(),
    TanStackRouterVite({ target: 'react', autoCodeSplitting: false }),
    tailwindcss(),
    VitePWA({
      registerType: 'autoUpdate',
      injectRegister: false,
      includeAssets: [
        'icons/icon-192.png',
        'icons/icon-512.png',
        'icons/apple-touch-icon.png',
      ],
      manifest: {
        name: 'Opções B3',
        short_name: 'Opções B3',
        description: 'Painel descritivo (EOD) de opções de ações da B3.',
        lang: 'pt-BR',
        display: 'standalone',
        orientation: 'portrait',
        theme_color: '#101418',
        background_color: '#101418',
        start_url: '/',
        scope: '/',
        icons: [
          { src: '/icons/icon-192.png', sizes: '192x192', type: 'image/png', purpose: 'any' },
          { src: '/icons/icon-512.png', sizes: '512x512', type: 'image/png', purpose: 'any' },
        ],
      },
      workbox: {
        // `injectRegister: false` desliga o default do plugin. Com
        // `registerType: 'autoUpdate'`, skipWaiting/clientsClaim precisam ser
        // explícitos; sem isso o SW novo fica "waiting" e a aba nunca sai do
        // bundle antigo sem hard refresh.
        skipWaiting: true,
        clientsClaim: true,
        globPatterns: ['**/*.{js,css,html,png,svg,woff2}'],
        navigateFallback: '/index.html',
        navigateFallbackDenylist: [/^\/api\//, /^\/mcp/],
        runtimeCaching: [
          {
            urlPattern: /\/api\/health\?probe=1/,
            handler: 'NetworkOnly',
          },
          {
            // Escritas do journal não podem ser sobrescritas por um GET cacheado.
            // O Workbox testa a URL completa: incluir `?` pega as listas paginadas.
            urlPattern: /\/api\/positions(?:[/?]|$)/,
            handler: 'NetworkOnly',
          },
          {
            urlPattern: /\/api\/journal(?:[/?]|$)/,
            handler: 'NetworkOnly',
          },
          {
            urlPattern: /\/api\//,
            handler: 'NetworkFirst',
            options: {
              cacheName: 'b3o-api',
              networkTimeoutSeconds: 4,
              expiration: { maxEntries: 80, maxAgeSeconds: 86_400 },
            },
          },
        ],
      },
    }),
  ],
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    copyPublicDir: false,
  },
})
