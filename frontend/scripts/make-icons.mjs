// Gera os ícones do manifest (192/512) sem dependência: PNG RGBA montado à mão.
// Monocromático grafite + agulha branco-gelo (docs/direcao.md), sem gradiente.
import { deflateSync } from 'node:zlib'
import { mkdirSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')

const BG = [0x10, 0x14, 0x18, 0xff]
const LINE = [0x5d, 0x6c, 0x7d, 0xff]
const INK = [0xe9, 0xf2, 0xf9, 0xff]

function crc32(buf) {
  let crc = 0xffffffff
  for (const byte of buf) {
    crc ^= byte
    for (let bit = 0; bit < 8; bit += 1) {
      crc = crc & 1 ? (crc >>> 1) ^ 0xedb88320 : crc >>> 1
    }
  }
  return (crc ^ 0xffffffff) >>> 0
}

function chunk(type, data) {
  const length = Buffer.alloc(4)
  length.writeUInt32BE(data.length)
  const body = Buffer.concat([Buffer.from(type, 'ascii'), data])
  const crc = Buffer.alloc(4)
  crc.writeUInt32BE(crc32(body))
  return Buffer.concat([length, body, crc])
}

function makeIcon(size) {
  const px = Buffer.alloc(size * size * 4)
  const set = (x, y, color) => {
    if (x < 0 || y < 0 || x >= size || y >= size) return
    const i = (y * size + x) * 4
    px[i] = color[0]
    px[i + 1] = color[1]
    px[i + 2] = color[2]
    px[i + 3] = color[3]
  }
  const rect = (x0, y0, w, h, color) => {
    for (let y = y0; y < y0 + h; y += 1) for (let x = x0; x < x0 + w; x += 1) set(x, y, color)
  }

  rect(0, 0, size, size, BG)
  const margin = Math.round(size * 0.18)
  const barY = Math.round(size * 0.62)
  const barH = Math.max(2, Math.round(size * 0.012))
  rect(margin, barY - Math.floor(barH / 2), size - 2 * margin, barH, LINE)

  const tickH = Math.round(size * 0.07)
  for (let i = 0; i <= 4; i += 1) {
    const x = margin + Math.round(((size - 2 * margin) * i) / 4)
    rect(x - 1, barY - Math.floor(tickH / 2), 2, tickH, LINE)
  }

  const needleX = Math.round(size * 0.62)
  const needleW = Math.max(2, Math.round(size * 0.012))
  const needleTop = Math.round(size * 0.34)
  const needleBottom = barY + Math.round(size * 0.09)
  rect(needleX - Math.floor(needleW / 2), needleTop, needleW, needleBottom - needleTop, INK)
  // ponta da agulha (triângulo curto)
  const tip = Math.round(size * 0.05)
  for (let row = 0; row < tip; row += 1) {
    const w = Math.max(1, Math.round((needleW * 3 * (tip - row)) / tip))
    rect(needleX - Math.floor(w / 2), needleTop - row - 1, w, 1, INK)
  }

  const raw = Buffer.alloc(size * (size * 4 + 1))
  for (let y = 0; y < size; y += 1) {
    raw[y * (size * 4 + 1)] = 0
    px.copy(raw, y * (size * 4 + 1) + 1, y * size * 4, (y + 1) * size * 4)
  }

  const ihdr = Buffer.alloc(13)
  ihdr.writeUInt32BE(size, 0)
  ihdr.writeUInt32BE(size, 4)
  ihdr[8] = 8
  ihdr[9] = 6
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk('IHDR', ihdr),
    chunk('IDAT', deflateSync(raw, { level: 9 })),
    chunk('IEND', Buffer.alloc(0)),
  ])
}

const outDir = join(root, 'public', 'icons')
mkdirSync(outDir, { recursive: true })
for (const size of [192, 512]) {
  const file = join(outDir, `icon-${size}.png`)
  writeFileSync(file, makeIcon(size))
  console.log(`escrito ${file} (${size}x${size})`)
}
