import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Check, X, Clock } from 'lucide-react'
import type { CatalogItem } from '@/lib/api'
import { relativeDay } from '@/lib/format'
import { cn } from '@/lib/utils'

/** Тема вопроса. Не «пилюля» — тихая метка. */
export function TopicTag({
  category,
  subcategory,
  className,
}: {
  category: string | null
  subcategory?: string | null
  className?: string
}) {
  if (!category) {
    return (
      <span className={cn('text-2xs text-muted-foreground/70', className)}>
        без темы
      </span>
    )
  }
  return (
    <span className={cn('text-2xs text-amber-ink', className)}>
      {category}
      {subcategory && <span className="text-muted-foreground"> · {subcategory}</span>}
    </span>
  )
}

/** Беручесть вопроса: доля команд, взявших его на турнире.
 *
 * Показываем именно её, а не сложность пакета: trueDL пакета одинаков для всех
 * вопросов турнира, и рядом с конкретным вопросом это число ничего не говорит.
 * В базе хранится difficulty = 10 × (1 − доля), разворачиваем обратно —
 * «взяли 84%» читается сразу, в отличие от «сл. 1.6».
 * Известна примерно у половины базы; если неизвестна — молчим.
 */
export function Difficulty({ value }: { value: number | null | undefined }) {
  if (value === null || value === undefined) return null
  const takePct = Math.round((10 - value) * 10)
  return (
    <span
      className="tabular text-2xs text-muted-foreground"
      title="Доля команд, взявших вопрос на турнире"
    >
      взяли {takePct}%
    </span>
  )
}

/** Статус изучения одной строкой. */
export function StatusMark({ item }: { item: CatalogItem }) {
  if (item.is_due) {
    return (
      <span
        className="inline-flex items-center gap-1 text-2xs text-amber-ink"
        title="Пора повторить"
      >
        <Clock className="size-3" strokeWidth={2} aria-hidden />
        повтор
      </span>
    )
  }
  if (!item.attempts_count) return null
  const knew = item.knew_any === 1
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 text-2xs',
        knew ? 'text-knew' : 'text-missed',
      )}
      title={`Попыток: ${item.attempts_count} · ${relativeDay(item.last_attempt_at)}`}
    >
      {knew ? (
        <Check className="size-3" strokeWidth={2.5} aria-hidden />
      ) : (
        <X className="size-3" strokeWidth={2.5} aria-hidden />
      )}
      {knew ? 'знал' : 'не знал'}
    </span>
  )
}

/** Плотная строка каталога. Вся строка — ссылка на вопрос.
 *
 * Текст свёрнут в две строки: список нужен для просмотра, и развёрнутые
 * простыни его ломают. Но прочитать вопрос целиком можно прямо здесь —
 * заходить внутрь и натыкаться на ответ ради этого не нужно.
 */
export function QuestionRow({ item }: { item: CatalogItem }) {
  const [expanded, setExpanded] = useState(false)
  const truncated = item.text.length > item.text_preview.length

  return (
    <Link
      to={`/question/${item.id}`}
      className="group block px-3 py-3 transition-colors hover:bg-amber-wash/40
                 focus-visible:bg-amber-wash/40"
    >
      <div className="mb-1 flex flex-wrap items-center gap-x-2.5 gap-y-1">
        <span className="tabular text-2xs text-muted-foreground/70">#{item.id}</span>
        <TopicTag category={item.category} subcategory={item.subcategory} />
        {item.year && (
          <span className="tabular text-2xs text-muted-foreground">{item.year}</span>
        )}
        <Difficulty value={item.question_difficulty} />
        <span className="ml-auto">
          <StatusMark item={item} />
        </span>
      </div>

      <p
        className={cn(
          'font-serif text-[15px] leading-[1.55] text-foreground/90',
          !expanded && 'line-clamp-2',
        )}
      >
        {expanded ? item.text : item.text_preview}
        {!expanded && truncated && '…'}
      </p>

      {(truncated || expanded) && (
        <button
          type="button"
          // Строка — ссылка на вопрос, поэтому гасим переход: развернуть текст
          // и уйти на страницу с ответом — разные намерения.
          onClick={(e) => {
            e.preventDefault()
            e.stopPropagation()
            setExpanded((v) => !v)
          }}
          className="mt-1 text-2xs text-amber-ink hover:underline"
        >
          {expanded ? 'свернуть' : 'читать целиком'}
        </button>
      )}

      {item.pack_title && (
        <p className="mt-1 truncate text-2xs text-muted-foreground/80">
          {item.pack_title}
        </p>
      )}
    </Link>
  )
}
