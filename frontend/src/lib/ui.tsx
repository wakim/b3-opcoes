import type { ReactNode } from 'react'
import { ErrorStatus } from '../components/State'
import { usePresentation } from './presentation'
import { note } from './classes'

export function ErrorBox({ error }: { error: Error | null }): ReactNode {
  if (!error) return null
  return <ErrorStatus error={error} tone="inline" />
}

export function Loading(): ReactNode {
  const p = usePresentation()
  return <p data-note="" className={`${note} lg:text-label`}>{p.copy('loading', 'generic').label}</p>
}
