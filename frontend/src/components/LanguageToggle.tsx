/** Seletor global de perfil de linguagem (F9).
 *
 * ToggleGroup de seleção única: `''` é ignorado, então uma opção fica sempre
 * marcada. Sem reload, sem escrita de API e sem invalidar queries — só o
 * contexto de apresentação muda. Sem pill, emoji, gradiente, cor nova,
 * uppercase ou tracking; alvo de 44 px nos dois eixos.
 */
import { useLanguage, type LanguageMode } from '../lib/language'
import { useCopy } from '../lib/presentation'
import { ToggleGroup, ToggleGroupItem } from './ui/toggle-group'

const ITEM =
  'min-h-[44px] min-w-[44px] rounded-[4px] border border-border bg-card px-[10px] py-[8px] text-[calc(11.5px*var(--font-scale))] font-normal whitespace-normal text-[color:var(--text-2)] shadow-none dark:bg-card hover:bg-muted hover:text-foreground data-[state=on]:border-input data-[state=on]:bg-muted data-[state=on]:text-foreground focus-visible:ring-0 focus-visible:outline-2 focus-visible:outline-solid focus-visible:-outline-offset-2 focus-visible:outline-primary lg:text-control'

export function LanguageToggle() {
  const { mode, setMode } = useLanguage()
  const group = useCopy('language', 'group')
  const expert = useCopy('language', 'expert')
  const leigo = useCopy('language', 'leigo')

  return (
    <ToggleGroup
      type="single"
      value={mode}
      onValueChange={(value) => {
        if (value) setMode(value as LanguageMode)
      }}
      className="flex shrink-0 flex-wrap gap-[4px]"
      aria-label={group.label}
    >
      <ToggleGroupItem value="expert" aria-label={expert.label} className={ITEM}>
        {expert.label}
      </ToggleGroupItem>
      <ToggleGroupItem value="leigo" aria-label={leigo.label} className={ITEM}>
        {leigo.label}
      </ToggleGroupItem>
    </ToggleGroup>
  )
}
