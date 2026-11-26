/** Perfil de linguagem global da F9 (Expert × Leigo).
 *
 * Um único contexto, acima do AppShell. A preferência é local ao aparelho
 * (`localStorage`); nenhuma query é invalidada quando o perfil muda — só o
 * texto visível muda. Leitura/escrita defensivas: storage ausente, corrompido
 * ou bloqueado cai para Expert sem quebrar a app.
 */
import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'

export type LanguageMode = 'expert' | 'leigo'

export interface LanguageContextValue {
  mode: LanguageMode
  setMode(mode: LanguageMode): void
}

/** Chave exata do contrato F9. Valores exatos: `expert` e `leigo`. */
export const LANGUAGE_STORAGE_KEY = 'b3o:language'

const DEFAULT_MODE: LanguageMode = 'expert'

function isMode(value: unknown): value is LanguageMode {
  return value === 'expert' || value === 'leigo'
}

function readMode(): LanguageMode {
  try {
    const raw = window.localStorage.getItem(LANGUAGE_STORAGE_KEY)
    return isMode(raw) ? raw : DEFAULT_MODE
  } catch {
    return DEFAULT_MODE
  }
}

function persistMode(mode: LanguageMode): void {
  try {
    window.localStorage.setItem(LANGUAGE_STORAGE_KEY, mode)
  } catch {
    // Storage cheio/bloqueado: a escolha ainda vale nesta sessão.
  }
}

/** Fallback fora do provider mantém a app viva (sem reload, sem erro). */
const fallback: LanguageContextValue = {
  mode: DEFAULT_MODE,
  setMode: () => {},
}

const LanguageContext = createContext<LanguageContextValue>(fallback)

export function LanguageProvider({ children }: { children: ReactNode }): ReactNode {
  const [mode, setModeState] = useState<LanguageMode>(readMode)

  useEffect(() => {
    function onStorage(event: StorageEvent) {
      // `localStorage.clear()` dispara com `key === null`: significa que a
      // preferência foi removida, então volta ao default sem regravar.
      if (event.key !== LANGUAGE_STORAGE_KEY && event.key !== null) return
      // Outra aba escreveu (ou limpou o storage): só sincroniza o estado,
      // nunca regrava a chave.
      setModeState(isMode(event.newValue) ? event.newValue : DEFAULT_MODE)
    }
    window.addEventListener('storage', onStorage)
    return () => window.removeEventListener('storage', onStorage)
  }, [])

  function setMode(next: LanguageMode) {
    setModeState(next)
    persistMode(next)
  }

  return <LanguageContext.Provider value={{ mode, setMode }}>{children}</LanguageContext.Provider>
}

export function useLanguage(): LanguageContextValue {
  return useContext(LanguageContext)
}
