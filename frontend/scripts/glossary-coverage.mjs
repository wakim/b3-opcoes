#!/usr/bin/env node
/**
 * Cobertura campo ↔ glossário (F5-C, ADR-019: script de conferência, não suíte).
 *
 * 1. Extrai o mapa `KEYS` de `src/components/metricKeys.ts` (campo → slug).
 * 2. Lê os campos usados como tocáveis nas rotas/componentes
 *    (`field="..."`, `openMetric('...')`, `onMetric('...')`).
 * 3. Confere que todo campo tocável tem slug e que todo slug do mapa existe em
 *    `GET /api/glossary`.
 *
 * Falha = lista do que falta + exit 1. Não inventa slug: o verbete entra em
 * `docs/glossario.md` e o mapa aponta para ele.
 *
 *   npm run coverage
 *   B3_HTTP_URL=http://127.0.0.1:8083 npm run coverage
 */
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { dirname, join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const root = join(here, '..')
const srcDir = join(root, 'src')

function walk(dir) {
  const out = []
  for (const name of readdirSync(dir)) {
    const full = join(dir, name)
    if (statSync(full).isDirectory()) out.push(...walk(full))
    else if (/\.(ts|tsx)$/.test(name)) out.push(full)
  }
  return out
}

const metricKeysPath = join(srcDir, 'components', 'metricKeys.ts')
const metricKeysSource = readFileSync(metricKeysPath, 'utf8')
const block = metricKeysSource.match(/const KEYS[^{]*\{([\s\S]*?)\n\}/)
if (!block) {
  console.error('coverage: não achei o bloco `const KEYS` em metricKeys.ts')
  process.exit(1)
}

const fieldToSlug = new Map()
for (const match of block[1].matchAll(/^\s*([A-Za-z0-9_]+):\s*'([^']+)'/gm)) {
  fieldToSlug.set(match[1], match[2])
}
if (fieldToSlug.size === 0) {
  console.error('coverage: mapa KEYS vazio')
  process.exit(1)
}

const used = new Map() // campo → arquivos
const patterns = [
  /field="([^"]+)"/g,
  /openMetric\(\s*'([^']+)'/g,
  /onMetric\(\s*'([^']+)'/g,
]

for (const file of walk(srcDir)) {
  const rel = relative(root, file)
  const source = readFileSync(file, 'utf8')
  for (const pattern of patterns) {
    for (const match of source.matchAll(pattern)) {
      const field = match[1]
      if (!used.has(field)) used.set(field, new Set())
      used.get(field).add(rel)
    }
  }
}

const missingSlug = [...used.entries()].filter(([field]) => !fieldToSlug.has(field))

const base = (process.env.B3_HTTP_URL ?? 'http://host.containers.internal:8080').replace(/\/$/, '')
let glossaryKeys
try {
  const response = await fetch(`${base}/api/glossary`, {
    headers: { Accept: 'application/json' },
  })
  const body = await response.json()
  if (!response.ok || !body?.data?.entries) {
    throw new Error(body?.error?.message ?? `HTTP ${response.status}`)
  }
  glossaryKeys = new Set(body.data.entries.map((entry) => entry.key))
} catch (cause) {
  console.error(`coverage: não consegui ler ${base}/api/glossary — ${cause.message}`)
  console.error('subiu o app? use B3_HTTP_URL=http://host.containers.internal:<porta>')
  process.exit(1)
}

const missingEntry = [...new Set(fieldToSlug.values())]
  .filter((slug) => !glossaryKeys.has(slug))
  .map((slug) => {
    const fields = [...fieldToSlug].filter(([, value]) => value === slug).map(([field]) => field)
    return { slug, fields }
  })

console.log(
  `coverage: ${used.size} campos tocáveis · ${fieldToSlug.size} campos no mapa · ` +
    `${glossaryKeys.size} verbetes servidos`,
)

let failed = false
if (missingSlug.length > 0) {
  failed = true
  console.error('\ncampos tocáveis SEM slug em metricKeys.ts:')
  for (const [field, files] of missingSlug) {
    console.error(`  - ${field} (${[...files].join(', ')})`)
  }
}
if (missingEntry.length > 0) {
  failed = true
  console.error('\nslugs do mapa SEM verbete em /api/glossary:')
  for (const { slug, fields } of missingEntry) {
    console.error(`  - ${slug} ← ${fields.join(', ')}`)
  }
}
if (failed) process.exit(1)

console.log('coverage: ok — todo campo tocável tem slug e todo slug existe no glossário')
