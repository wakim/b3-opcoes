/// <reference types="vite/client" />
/// <reference types="vite-plugin-pwa/client" />

// Tipos mínimos de `node:fs`/`node:path` usados pelo `vite.config.ts` (plugin de
// assets do `public/`). Evita `@types/node` como dependência nova: o config roda
// no Node do container, mas o único uso é ler/emitir arquivos no build.
declare module 'node:fs' {
  export function readFileSync(path: string): Uint8Array
  export function readdirSync(path: string): string[]
  export function statSync(path: string): { isDirectory(): boolean }
}

declare module 'node:path' {
  export function join(...parts: string[]): string
  export function relative(from: string, to: string): string
}
