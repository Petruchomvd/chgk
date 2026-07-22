import { Link, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { ArrowRight, Check, X } from 'lucide-react'
import { api } from '@/lib/api'
import { Page, PageHeader } from '@/components/AppShell'
import { Empty, ErrorState, BlockSkeleton, loadError } from '@/components/States'
import { Button } from '@/components/ui/button'
import { num, questionsWord, relativeDay, modeLabel, plural, verbAgrees } from '@/lib/format'
import { cn } from '@/lib/utils'

/** Статистика строкой: число крупно, подпись тихо. Без карточек на каждый факт. */
function Stat({ value, label }: { value: string; label: string }) {
  return (
    <div>
      <div className="tabular font-serif text-[22px] leading-none font-semibold">{value}</div>
      <div className="mt-1 text-2xs text-muted-foreground">{label}</div>
    </div>
  )
}

function Section({
  title,
  action,
  children,
}: {
  title: string
  action?: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <section className="mt-8">
      <div className="mb-2.5 flex items-baseline justify-between gap-3 border-b border-border pb-1.5">
        <h2 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
          {title}
        </h2>
        {action}
      </div>
      {children}
    </section>
  )
}

export function Overview() {
  const navigate = useNavigate()
  const { data, isPending, error, fetchStatus, refetch } = useQuery({
    queryKey: ['overview'],
    queryFn: api.overview,
  })
  const err = loadError(error, fetchStatus)

  if (err) {
    return (
      <Page>
        <PageHeader title="Обзор" />
        <ErrorState error={err} onRetry={() => refetch()} />
      </Page>
    )
  }

  if (isPending || !data) {
    return (
      <Page>
        <PageHeader title="Обзор" />
        <BlockSkeleton className="h-20" />
        <BlockSkeleton className="mt-8 h-40" />
      </Page>
    )
  }

  const { stats, due_count, weak_categories, recent, active_session } = data
  const pct = stats.total_attempts
    ? Math.round((100 * stats.correct_attempts) / stats.total_attempts)
    : null
  const untouched = !stats.total_attempts

  return (
    <Page>
      <PageHeader
        title="Обзор"
        meta={
          untouched
            ? undefined
            : `${num(stats.distinct_questions)} ${questionsWord(stats.distinct_questions)} ${verbAgrees(stats.distinct_questions, 'пройден', 'пройдено')}`
        }
      />

      {/* ─── Главное действие ──────────────────────────────────── */}
      {active_session ? (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-amber/40 bg-amber-wash/50 px-4 py-3.5">
          <div className="min-w-0">
            <p className="text-sm font-medium">Незавершённая тренировка</p>
            <p className="mt-0.5 text-xs text-muted-foreground">
              {active_session.filters_repr} · вопрос {active_session.index + 1} из{' '}
              {active_session.total}
            </p>
          </div>
          <Button onClick={() => navigate(`/training/${active_session.session_id}`)}>
            Продолжить
            <ArrowRight className="size-3.5" aria-hidden />
          </Button>
        </div>
      ) : due_count > 0 ? (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-amber/40 bg-amber-wash/50 px-4 py-3.5">
          <div className="min-w-0">
            <p className="text-sm font-medium">
              {num(due_count)} {questionsWord(due_count)}{' '}
              {verbAgrees(due_count, 'ждёт', 'ждут')} повторения
            </p>
            <p className="mt-0.5 text-xs text-muted-foreground">
              Интервальное повторение: чем раньше, тем прочнее запоминается.
            </p>
          </div>
          <Button onClick={() => navigate('/review')}>
            Повторить
            <ArrowRight className="size-3.5" aria-hidden />
          </Button>
        </div>
      ) : (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border bg-paper-raised px-4 py-3.5">
          <div className="min-w-0">
            <p className="text-sm font-medium">
              {untouched ? 'Начните с первой тренировки' : 'На сегодня повторений нет'}
            </p>
            <p className="mt-0.5 text-xs text-muted-foreground">
              {untouched
                ? 'Результаты сохраняются: пройденные вопросы вернутся на повторение.'
                : 'Можно взять новые вопросы или потренироваться по теме.'}
            </p>
          </div>
          <Button onClick={() => navigate('/training')}>
            Тренировка
            <ArrowRight className="size-3.5" aria-hidden />
          </Button>
        </div>
      )}

      {/* ─── Статистика ────────────────────────────────────────── */}
      {!untouched && (
        <div className="mt-6 grid grid-cols-2 gap-x-4 gap-y-5 sm:grid-cols-4">
          <Stat value={num(stats.total_attempts)} label="попыток всего" />
          <Stat value={pct === null ? '—' : `${pct}%`} label="верных ответов" />
          <Stat value={num(stats.distinct_questions)} label="разных вопросов" />
          <Stat value={num(due_count)} label="к повторению" />
        </div>
      )}

      {/* ─── Слабые темы ───────────────────────────────────────── */}
      <Section
        title="Слабые темы"
        action={
          weak_categories.length > 0 ? (
            <Link
              to="/topics"
              className="text-2xs text-amber-ink underline-offset-2 hover:underline"
            >
              все темы
            </Link>
          ) : undefined
        }
      >
        {weak_categories.length === 0 ? (
          <Empty
            title="Пока недостаточно данных"
            hint="Тема попадает сюда после трёх и более попыток — иначе процент случаен."
          />
        ) : (
          <ul className="divide-y divide-border">
            {weak_categories.map((w) => (
              <li key={w.category}>
                <Link
                  to={`/catalog?category=${encodeURIComponent(w.category)}`}
                  className="flex items-center gap-3 py-2 transition-colors hover:bg-amber-wash/40"
                >
                  <span className="min-w-0 flex-1 truncate text-[13px]">{w.category}</span>
                  {/* Полоса = доля верных. Единственный график, который влияет на выбор. */}
                  <span className="hidden h-1.5 w-28 overflow-hidden rounded-sm bg-paper-sunk sm:block">
                    <span
                      className={cn(
                        'block h-full',
                        w.success_pct < 50 ? 'bg-missed' : 'bg-amber',
                      )}
                      style={{ width: `${Math.max(2, w.success_pct)}%` }}
                    />
                  </span>
                  <span className="tabular w-9 text-right text-xs font-medium">
                    {w.success_pct}%
                  </span>
                  <span className="tabular w-[86px] shrink-0 text-right text-2xs whitespace-nowrap text-muted-foreground">
                    {w.attempts_count} {plural(w.attempts_count, 'попытка', 'попытки', 'попыток')}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </Section>

      {/* ─── Последние вопросы ─────────────────────────────────── */}
      <Section title="Недавно">
        {recent.length === 0 ? (
          <Empty
            title="История пуста"
            hint="Здесь появятся вопросы, которые вы разбирали."
          />
        ) : (
          <ul className="divide-y divide-border">
            {recent.map((r, i) => (
              <li key={`${r.question_id}-${i}`}>
                <Link
                  to={`/question/${r.question_id}`}
                  className="flex gap-3 py-2.5 transition-colors hover:bg-amber-wash/40"
                >
                  {r.knew ? (
                    <Check className="mt-0.5 size-3.5 shrink-0 text-knew" strokeWidth={2.5} aria-hidden />
                  ) : (
                    <X className="mt-0.5 size-3.5 shrink-0 text-missed" strokeWidth={2.5} aria-hidden />
                  )}
                  <span className="min-w-0 flex-1">
                    <span className="line-clamp-2 font-serif text-[13px] leading-snug text-foreground/90">
                      {r.text_preview}…
                    </span>
                    <span className="mt-1 block text-2xs text-muted-foreground">
                      {r.category ?? 'без темы'} · {modeLabel(r.mode)} ·{' '}
                      {relativeDay(r.attempted_at)}
                    </span>
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </Section>
    </Page>
  )
}
