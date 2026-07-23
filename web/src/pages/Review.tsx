import { useNavigate } from 'react-router-dom'
import { useMutation, useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { Page, PageHeader } from '@/components/AppShell'
import { Empty, ErrorState, RowSkeleton, loadError } from '@/components/States'
import { QuestionRow } from '@/components/QuestionBits'
import { Button } from '@/components/ui/button'
import { num, questionsWord, LEITNER_DAYS } from '@/lib/format'

const BOX_LABEL: Record<number, string> = {
  1: 'ошибка замечена',
  2: 'первое закрепление',
  3: 'помню неделю',
  4: 'помню две недели',
  5: 'закреплено',
}

export function Review() {
  const navigate = useNavigate()

  const { data: overview, isPending: loadingOverview, error, fetchStatus, refetch } = useQuery({
    queryKey: ['overview'],
    queryFn: api.overview,
  })

  // Список к повторению — тот же каталог со статусом «due».
  const { data: due, isPending: loadingDue } = useQuery({
    queryKey: ['questions', { status: 'due' }],
    queryFn: () => api.questions({ status: 'due', limit: 30 }),
  })

  const start = useMutation({
    mutationFn: () => api.startTraining({ mode: 'review', count: 12 }),
    onSuccess: (s) => navigate(`/training/${s.session_id}`),
  })

  const err = loadError(error, fetchStatus)

  if (err) {
    return (
      <Page>
        <PageHeader title="Повторение" />
        <ErrorState error={err} onRetry={() => refetch()} />
      </Page>
    )
  }

  const dueCount = overview?.due_count ?? 0
  const boxes = overview?.stats.by_box ?? []
  const totalTracked = boxes.reduce((s, b) => s + b.c, 0)

  return (
    <Page wide>
      <PageHeader
        title="Повторение"
        meta={dueCount > 0 ? `${num(dueCount)} на сегодня` : undefined}
        actions={
          dueCount > 0 ? (
            <Button onClick={() => start.mutate()} disabled={start.isPending}>
              {start.isPending ? 'Загружаем…' : 'Повторить 12'}
            </Button>
          ) : undefined
        }
      />

      {start.error && (
        <div className="mb-4">
          <ErrorState error={start.error} />
        </div>
      )}

      {/* ─── Распределение по коробкам ─────────────────────────── */}
      {totalTracked > 0 && (
        <section className="mb-6">
          <h2 className="mb-2 text-xs font-medium tracking-wide text-muted-foreground uppercase">
            Как распределены вопросы
          </h2>
          <ul className="divide-y divide-border border-y border-border">
            {[1, 2, 3, 4, 5].map((box) => {
              const c = boxes.find((b) => b.box === box)?.c ?? 0
              const pct = totalTracked ? (100 * c) / totalTracked : 0
              return (
                <li key={box} className="flex items-center gap-3 py-2">
                  <span className="tabular w-16 shrink-0 text-2xs text-muted-foreground">
                    коробка {box}
                  </span>
                  <span className="w-32 shrink-0 text-2xs text-muted-foreground/80">
                    {BOX_LABEL[box]}
                  </span>
                  <span className="h-1.5 flex-1 overflow-hidden rounded-sm bg-paper-sunk">
                    <span
                      className="block h-full bg-amber-soft"
                      style={{ width: `${Math.max(c ? 1.5 : 0, pct)}%` }}
                    />
                  </span>
                  <span className="tabular w-10 text-right text-2xs">{num(c)}</span>
                  <span className="tabular hidden w-20 text-right text-2xs text-muted-foreground sm:block">
                    +{LEITNER_DAYS[box]} дн.
                  </span>
                </li>
              )
            })}
          </ul>
        </section>
      )}

      {/* ─── Очередь ───────────────────────────────────────────── */}
      <h2 className="mb-2 text-xs font-medium tracking-wide text-muted-foreground uppercase">
        Ждут повторения
      </h2>

      <div className="overflow-hidden rounded-lg border border-border bg-paper-raised">
        {loadingOverview || loadingDue ? (
          <RowSkeleton count={5} />
        ) : dueCount === 0 ? (
          <Empty
            className="border-0 bg-transparent"
            title={totalTracked === 0 ? 'Повторять пока нечего' : 'На сегодня всё повторено'}
            hint={
              totalTracked === 0
                ? 'Сюда попадут только вопросы, в которых была ошибка.'
                : 'Ошибочные вопросы вернутся, когда подойдёт их интервал. Можно взять новые.'
            }
            action={
              <Button variant="outline" size="sm" onClick={() => navigate('/training')}>
                К тренировке
              </Button>
            }
          />
        ) : (
          <ul className="divide-y divide-border">
            {due?.items.map((item) => (
              <li key={item.id}>
                <QuestionRow item={item} />
              </li>
            ))}
          </ul>
        )}
      </div>

      {dueCount > 30 && (
        <p className="mt-3 text-2xs text-muted-foreground">
          Показаны первые 30 из {num(dueCount)} {questionsWord(dueCount)}.
        </p>
      )}
    </Page>
  )
}
