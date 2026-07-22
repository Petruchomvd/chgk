import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useMutation, useQuery } from '@tanstack/react-query'
import { ChevronRight, Play } from 'lucide-react'
import { api, type TopicCategory } from '@/lib/api'
import { Page, PageHeader } from '@/components/AppShell'
import { Empty, ErrorState, BlockSkeleton, loadError } from '@/components/States'
import { Button } from '@/components/ui/button'
import { num, questionsWord } from '@/lib/format'
import { cn } from '@/lib/utils'

function Row({ cat }: { cat: TopicCategory }) {
  const [open, setOpen] = useState(false)
  const navigate = useNavigate()

  const start = useMutation({
    mutationFn: () =>
      api.startTraining({ mode: 'category', category_ids: [cat.category_id], count: 12 }),
    onSuccess: (s) => navigate(`/training/${s.session_id}`),
  })

  const hasStats = cat.success_pct !== null

  return (
    <li className="border-b border-border last:border-0">
      <div className="group flex items-center gap-3 py-2.5">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          className="flex min-w-0 flex-1 items-center gap-2 text-left"
        >
          <ChevronRight
            className={cn(
              'size-3.5 shrink-0 text-muted-foreground transition-transform',
              open && 'rotate-90',
            )}
            aria-hidden
          />
          <span className="truncate text-[13px] font-medium">{cat.category}</span>
          <span className="tabular shrink-0 text-2xs text-muted-foreground">
            {num(cat.questions_count)}
          </span>
        </button>

        {/* Результат по теме — только если попыток достаточно, иначе процент врёт. */}
        {hasStats ? (
          <>
            <span className="hidden h-1.5 w-24 overflow-hidden rounded-sm bg-paper-sunk sm:block">
              <span
                className={cn(
                  'block h-full',
                  cat.success_pct! < 50 ? 'bg-missed' : cat.success_pct! < 75 ? 'bg-amber' : 'bg-knew',
                )}
                style={{ width: `${Math.max(2, cat.success_pct!)}%` }}
              />
            </span>
            <span className="tabular w-9 text-right text-xs font-medium">
              {cat.success_pct}%
            </span>
            <span className="tabular hidden w-24 text-right text-2xs text-muted-foreground sm:block">
              {num(cat.distinct_questions)} пройдено
            </span>
          </>
        ) : (
          <span className="hidden text-2xs text-muted-foreground/60 sm:block sm:w-[164px] sm:text-right">
            нет попыток
          </span>
        )}

        <Button
          variant="ghost"
          size="icon-sm"
          onClick={() => start.mutate()}
          disabled={start.isPending || cat.questions_count === 0}
          aria-label={`Тренировка по теме «${cat.category}»`}
          title={`Тренировка по теме «${cat.category}»`}
          // Всегда видима: на тач-устройствах ховера нет, а действие частое.
          className="shrink-0 text-muted-foreground hover:text-amber-ink"
        >
          <Play className="size-3.5" aria-hidden />
        </Button>
      </div>

      {open && (
        <ul className="mb-2 ml-5 space-y-0.5 border-l border-border pl-4">
          {cat.subcategories.length === 0 ? (
            <li className="py-1 text-2xs text-muted-foreground">Подтем нет</li>
          ) : (
            cat.subcategories.map((s) => (
              <li key={s.subcategory_id}>
                <Link
                  to={`/catalog?category=${encodeURIComponent(cat.category)}`}
                  className="flex items-center justify-between gap-3 py-1 text-xs
                             text-muted-foreground transition-colors hover:text-amber-ink"
                >
                  <span className="truncate">{s.subcategory}</span>
                  <span className="tabular shrink-0 text-2xs">{num(s.questions_count)}</span>
                </Link>
              </li>
            ))
          )}
        </ul>
      )}
    </li>
  )
}

export function Topics() {
  // isPending, а не isLoading: «данных ещё нет» не должно рендериться как «пусто».
  const { data, isPending, error, fetchStatus, refetch } = useQuery({
    queryKey: ['topics'],
    queryFn: api.topics,
  })
  const err = loadError(error, fetchStatus)
  const { data: meta } = useQuery({ queryKey: ['meta'], queryFn: api.meta, staleTime: Infinity })

  if (err) {
    return (
      <Page>
        <PageHeader title="Темы" />
        <ErrorState error={err} onRetry={() => refetch()} />
      </Page>
    )
  }

  const cats = data?.categories ?? []
  const classified = meta?.classified ?? 0

  return (
    <Page>
      <PageHeader
        title="Темы"
        meta={cats.length ? `${cats.length} категорий` : undefined}
      />

      {/* Честно про покрытие: темы known только для размеченной части базы. */}
      {meta && meta.classification_pct < 100 && (
        <p className="mb-4 border-l-2 border-amber pl-3 text-xs leading-relaxed text-muted-foreground">
          Темы известны у {num(classified)} из {num(meta.total_questions)}{' '}
          {questionsWord(meta.total_questions)} — это {meta.classification_pct}% базы.
          Остальные вопросы доступны в{' '}
          <Link to="/catalog" className="text-amber-ink underline-offset-2 hover:underline">
            картотеке
          </Link>{' '}
          и в случайной тренировке.
        </p>
      )}

      {isPending ? (
        <BlockSkeleton className="h-64" />
      ) : cats.length === 0 ? (
        <Empty
          title="Тем пока нет"
          hint="База ещё не классифицирована. Запустите классификацию, чтобы темы появились."
        />
      ) : (
        <ul className="rounded-lg border border-border bg-paper-raised px-3">
          {cats.map((c) => (
            <Row key={c.category_id} cat={c} />
          ))}
        </ul>
      )}
    </Page>
  )
}
