import { useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Sparkles } from 'lucide-react'
import { api } from '@/lib/api'
import { Page } from '@/components/AppShell'
import { ErrorState, BlockSkeleton, loadError } from '@/components/States'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'

/** Поиск по смыслу (эмбеддинги): находит вопросы про факт, даже если
 *  в них нет слов запроса — «корсиканец-император» найдётся по «Наполеону». */
export function Search() {
  const [params, setParams] = useSearchParams()
  const submitted = params.get('q') ?? ''
  const [draft, setDraft] = useState(submitted)

  const { data, isFetching, error, fetchStatus, refetch } = useQuery({
    queryKey: ['semantic-search', submitted],
    queryFn: () => api.semanticSearch(submitted, 20),
    enabled: submitted.trim().length >= 3,
  })

  const err = loadError(error, fetchStatus)

  const submit = (e: React.FormEvent) => {
    e.preventDefault()
    const q = draft.trim()
    if (q.length >= 3) setParams({ q })
  }

  return (
    <Page>
      <h1 className="mb-1 text-lg font-semibold">Поиск по смыслу</h1>
      <p className="mb-5 text-xs text-muted-foreground">
        Ищет вопросы про факт или тему, даже если слова запроса в них не
        встречаются. Первый запрос после запуска сервера думает несколько
        секунд — грузится модель.
      </p>

      <form onSubmit={submit} className="mb-6 flex gap-2">
        <Input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="например: кочерга Витгенштейна и Поппер"
          className="h-9"
        />
        <Button type="submit" className="h-9 shrink-0" disabled={draft.trim().length < 3}>
          <Sparkles className="size-3.5" aria-hidden />
          Найти
        </Button>
      </form>

      {err ? <ErrorState error={err} onRetry={() => refetch()} /> : null}

      {isFetching && (
        <>
          <BlockSkeleton className="h-20" />
          <BlockSkeleton className="mt-3 h-20" />
        </>
      )}

      {data && !isFetching && !data.available && (
        <p className="text-xs text-muted-foreground">
          Поиск по смыслу доступен только там, где установлена модель и лежат векторы.
        </p>
      )}

      {data && !isFetching && data.available && data.items.length > 0 && (
        <ul className="divide-y divide-border border-y border-border">
          {data.items.map((it) => (
            <li key={it.id} className="py-3">
              <Link to={`/question/${it.id}`} className="group block">
                <div className="mb-1 flex items-center gap-2 text-2xs text-muted-foreground">
                  <span className="tabular rounded bg-amber-wash/60 px-1.5 py-0.5 text-amber-ink">
                    Сходство {Math.round(it.similarity * 100)}%
                  </span>
                  {it.question_difficulty !== null && (
                    <span className="tabular text-muted-foreground">
                      Взяли {Math.round((10 - it.question_difficulty) * 10)}%
                    </span>
                  )}
                  {it.category && <span className="text-amber-ink">{it.category}</span>}
                  {it.pack_title && (
                    <span className="truncate">
                      {it.pack_title}
                      {it.year && ` · ${it.year}`}
                    </span>
                  )}
                </div>
                <p className="text-xs leading-relaxed group-hover:text-amber-ink">
                  {it.text_preview}…
                </p>
                <p className="mt-0.5 text-2xs text-muted-foreground">
                  Ответ: {it.answer}
                </p>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </Page>
  )
}
