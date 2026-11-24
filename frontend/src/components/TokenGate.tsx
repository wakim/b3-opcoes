/** Token de escrita do PWA: digitado uma vez, salvo só no localStorage. */
import { useCallback, useEffect, useId, useState, type FormEvent } from 'react'
import { APP_TOKEN_STORAGE_KEY } from '../lib/api'
import { Button } from './ui/button'
import { Input } from './ui/input'

const APP_TOKEN_EVENT = 'b3o:app-token-change'

function storedToken(): string {
  try {
    return (typeof localStorage === 'undefined' ? '' : localStorage.getItem(APP_TOKEN_STORAGE_KEY))?.trim() ?? ''
  } catch {
    return ''
  }
}

function writeStoredToken(value: string | null): boolean {
  try {
    if (value === null) localStorage.removeItem(APP_TOKEN_STORAGE_KEY)
    else localStorage.setItem(APP_TOKEN_STORAGE_KEY, value)
    window.dispatchEvent(new Event(APP_TOKEN_EVENT))
    return true
  } catch {
    return false
  }
}

/** Estado compartilhado entre Carteira e detalhe; o valor do token nunca sai daqui. */
export function useAppToken() {
  const [hasToken, setHasToken] = useState(() => Boolean(storedToken()))

  useEffect(() => {
    const sync = () => setHasToken(Boolean(storedToken()))
    const onStorage = (event: StorageEvent) => {
      if (event.key === null || event.key === APP_TOKEN_STORAGE_KEY) sync()
    }
    window.addEventListener(APP_TOKEN_EVENT, sync)
    window.addEventListener('storage', onStorage)
    return () => {
      window.removeEventListener(APP_TOKEN_EVENT, sync)
      window.removeEventListener('storage', onStorage)
    }
  }, [])

  const forgetToken = useCallback(() => {
    if (!writeStoredToken(null)) return false
    setHasToken(false)
    return true
  }, [])

  return { hasToken, forgetToken }
}

export function TokenGate({
  onUnlock,
  notice,
  compact = false,
  title,
  autoFocus = false,
  description,
}: {
  onUnlock?: () => void
  notice?: string | null
  compact?: boolean
  title?: string
  autoFocus?: boolean
  description?: string
}) {
  const id = useId()
  const [value, setValue] = useState('')
  const [error, setError] = useState<string | null>(null)

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const token = value.trim()
    if (!token) {
      setError('digite o token do aplicativo')
      return
    }
    if (!writeStoredToken(token)) {
      setError('não foi possível salvar o token neste navegador')
      return
    }
    setValue('')
    setError(null)
    onUnlock?.()
  }

  return (
    <section className={`token-gate${compact ? ' token-gate--compact' : ''}`} aria-labelledby={`${id}-title`}>
      <h2 className="block-title lg:text-block-title lg:leading-title" id={`${id}-title`}>
        {title ?? (compact ? 'Token para fechar' : 'Liberar escritas')}
      </h2>
      <p className="chain-note lg:text-label lg:leading-body">
        {description ??
          (compact
            ? 'O fechamento grava um evento no journal e exige o token de escrita.'
            : 'A leitura é liberada na tailnet. O token libera apenas as escritas desta carteira.')}
      </p>
      <form className="token-gate__form" onSubmit={submit}>
        <label className="builder__field token-gate__field" htmlFor={`${id}-token`}>
          <span className="builder__field-label lg:text-label">Token do aplicativo</span>
          <Input
            id={`${id}-token`}
            type="password"
            value={value}
            onChange={(event) => setValue(event.target.value)}
            autoComplete="off"
            autoCapitalize="none"
            spellCheck={false}
            aria-describedby={`${id}-note`}
            data-autofocus
            autoFocus={autoFocus}
          />
        </label>
        <Button type="submit">Salvar token</Button>
      </form>
      {(notice || error) && (
        <p className="notice lg:text-label" role="alert">
          {error ?? notice}
        </p>
      )}
      <p className="note lg:text-label" id={`${id}-note`}>
        Fica somente neste navegador e é enviado no cabeçalho das escritas. Não aparece na tela nem no
        log.
      </p>
    </section>
  )
}
