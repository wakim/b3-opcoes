import type { ReactNode } from 'react'
import { ErrorStatus } from '../components/State'
import { note } from './classes'

export function ErrorBox({ error }: { error: Error | null }): ReactNode {
  if (!error) return null
  return <ErrorStatus error={error} tone="inline" />
}

export function Loading(): ReactNode {
  return <p data-note="" className={`${note} lg:text-label`}>carregando…</p>
}
