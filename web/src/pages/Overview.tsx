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

function TopicRows({
  items,
  tone,
}: {
  items: OverviewData['strong_categories']
  tone: 'strong' | 'weak'
}) {
  return (
    <ul className="divide-y divide-border">
      {items.map((item) => (
        <li key={item.category}>
          <Link
            to={`/catalog?category=${encodeURIComponent(item.category)}`}
            className="flex items-center gap-3 py-2 transition-colors hover:bg-amber-wash/40"
          >
            <span className="min-w-0 flex-1 truncate text-[13px]">{item.category}</span>
            <span className="hidden h-1.5 w-28 overflow-hidden rounded-sm bg-paper-sunk sm:block">
              <span
                className={cn(
                  'block h-full',
                  tone === 'strong' ? 'bg-knew' : 'bg-missed',
                )}
                style={{ width: `${Math.max(2, item.success_pct)}%` }}
              />
            </span>
            <span className="tabular w-9 text-right text-xs font-medium">
              {item.success_pct}%
            </span>
            <span className="tabular w-[86px] shrink-0 text-right text-2xs whitespace-nowrap text-muted-foreground">
              {item.attempts_count}{' '}
              {plural(item.attempts_count, 'попытка', 'попытки', 'попыток')}
            </span>
          </Link>
        </li>
      ))}
    </ul>
  )
}

function formatShortDay(day: string) {
  const [, month, date] = day.split('-')
  return `${date}.${month}`
}

function buildProgressTimeline(activity: OverviewData['activity']) {
  const byDay = new Map(activity.map((item) => [item.day, item]))
  const days: OverviewData['activity'] = []
  const today = new Date()
  for (let offset = 29; offset >= 0; offset -= 1) {
    const d = new Date(today)
    d.setDate(today.getDate() - offset)
    const key = d.toISOString().slice(0, 10)
    days.push(byDay.get(key) ?? { day: key, total: 0, knew: 0 })
  }
  return days
}

function ProgressHistory({ activity }: { activity: OverviewData['activity'] }) {
  const days = buildProgressTimeline(activity)
  const activeDays = days.filter((day) => day.total > 0)
  const maxTotal = Math.max(...days.map((day) => day.total), 1)
  const lastActive = activeDays.slice(-7)
  const previousActive = activeDays.slice(-14, -7)
  const sum = (items: typeof activeDays, key: 'total' | 'knew') =>
    items.reduce((acc, item) => acc + item[key], 0)
  const success = (items: typeof activeDays) => {
    const total = sum(items, 'total')
    return total ? Math.round((100 * sum(items, 'knew')) / total) : null
  }
  const recentSuccess = success(lastActive)
  const previousSuccess = success(previousActive)
  const trend =
    recentSuccess == null || previousSuccess == null
      ? null
      : recentSuccess - previousSuccess
  const totalQuestions = sum(activeDays, 'total')
  const totalCorrect = sum(activeDays, 'knew')
  const bestDay = activeDays.reduce<(typeof activeDays)[number] | null>(
    (best, day) =>
      !best || day.total > best.total || (day.total === best.total && day.knew > best.knew)
        ? day
        : best,
    null,
  )
  const chartWidth = 720
  const chartHeight = 180
  const chartPadding = { top: 16, right: 18, bottom: 30, left: 24 }
  const innerWidth = chartWidth - chartPadding.left - chartPadding.right
  const innerHeight = chartHeight - chartPadding.top - chartPadding.bottom
  const slot = innerWidth / days.length
  const barWidth = Math.max(8, slot * 0.58)
  const points = days
    .map((day, index) => {
      if (!day.total) return null
      const pct = (100 * day.knew) / day.total
      const x = chartPadding.left + slot * index + slot / 2
      const y = chartPadding.top + innerHeight - (pct / 100) * innerHeight
      return { x, y, pct, day }
    })
    .filter((point): point is NonNullable<typeof point> => point !== null)
  const linePath = points
    .map((point, index) => `${index === 0 ? 'M' : 'L'} ${point.x.toFixed(1)} ${point.y.toFixed(1)}`)
    .join(' ')

  if (activeDays.length === 0) {
    return <Empty title="Пока нет активности" />
  }

  return (
    <div className="rounded-lg border border-border bg-paper-raised">
      <div className="grid gap-4 border-b border-border px-4 py-3.5 sm:grid-cols-[1fr_auto]">
        <div>
          <p className="text-sm font-medium">История прогресса</p>
          <p className="mt-1 text-xs text-muted-foreground">
            Столбики — сколько вопросов было в день, цвет — насколько удачно.
          </p>
        </div>
        <div className="grid grid-cols-3 gap-4 text-right">
          <Stat
            value={`${Math.round((100 * totalCorrect) / Math.max(totalQuestions, 1))}%`}
            label="за 30 дней"
          />
          <Stat
            value={trend == null ? '—' : `${trend > 0 ? '+' : ''}${trend} п.п.`}
            label="тренд"
          />
          <Stat value={num(totalQuestions)} label="вопросов" />
        </div>
      </div>

      <div className="px-4 py-4">
        <div className="overflow-hidden rounded-md border border-border/70 bg-paper">
          <svg
            viewBox={`0 0 ${chartWidth} ${chartHeight}`}
            role="img"
            aria-label="График активности и процента правильных ответов за 30 дней"
            className="block h-[220px] w-full sm:h-[260px]"
            preserveAspectRatio="none"
          >
            <line
              x1={chartPadding.left}
              y1={chartPadding.top + innerHeight}
              x2={chartWidth - chartPadding.right}
              y2={chartPadding.top + innerHeight}
              className="stroke-border"
              strokeWidth="1"
            />
            {[25, 50, 75].map((mark) => {
              const y = chartPadding.top + innerHeight - (mark / 100) * innerHeight
              return (
                <g key={mark}>
                  <line
                    x1={chartPadding.left}
                    y1={y}
                    x2={chartWidth - chartPadding.right}
                    y2={y}
                    className="stroke-border/60"
                    strokeDasharray="4 6"
                    strokeWidth="1"
                  />
                  <text
                    x={chartPadding.left - 6}
                    y={y + 4}
                    textAnchor="end"
                    className="fill-muted-foreground text-[10px]"
                  >
                    {mark}%
                  </text>
                </g>
              )
            })}

            {days.map((day, index) => {
              const pct = day.total ? Math.round((100 * day.knew) / day.total) : null
              const barHeight = day.total ? Math.max(7, (day.total / maxTotal) * innerHeight) : 2
              const x = chartPadding.left + slot * index + (slot - barWidth) / 2
              const y = chartPadding.top + innerHeight - barHeight
              const fill =
                pct == null
                  ? 'fill-paper-sunk'
                  : pct >= 55
                    ? 'fill-knew'
                    : pct >= 30
                      ? 'fill-amber'
                      : 'fill-missed'
              return (
                <g key={day.day}>
                  <rect
                    x={x}
                    y={y}
                    width={barWidth}
                    height={barHeight}
                    rx="3"
                    className={cn(fill, 'opacity-75')}
                  >
                    <title>
                      {day.total
                        ? `${formatShortDay(day.day)}: ${day.knew}/${day.total} (${pct}%)`
                        : `${formatShortDay(day.day)}: не тренировались`}
                    </title>
                  </rect>
                  {(index === 0 || index === days.length - 1 || index === 14) && (
                    <text
                      x={chartPadding.left + slot * index + slot / 2}
                      y={chartHeight - 10}
                      textAnchor="middle"
                      className="fill-muted-foreground text-[10px]"
                    >
                      {formatShortDay(day.day)}
                    </text>
                  )}
                </g>
              )
            })}

            {linePath && (
              <path
                d={linePath}
                fill="none"
                className="stroke-foreground"
                strokeWidth="2.5"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            )}
            {points.map((point) => (
              <circle
                key={`${point.day.day}-${point.x}`}
                cx={point.x}
                cy={point.y}
                r="4"
                className="fill-paper stroke-foreground"
                strokeWidth="2"
              >
                <title>
                  {formatShortDay(point.day.day)}: {Math.round(point.pct)}% правильных
                </title>
              </circle>
            ))}
          </svg>
        </div>

        <div className="mt-6 grid gap-3 text-xs text-muted-foreground sm:grid-cols-3">
          <p>
            Последние активные дни:{' '}
            <span className="font-medium text-foreground">
              {recentSuccess == null ? 'пока мало данных' : `${recentSuccess}%`}
            </span>
          </p>
          <p>
            До этого:{' '}
            <span className="font-medium text-foreground">
              {previousSuccess == null ? 'нет базы сравнения' : `${previousSuccess}%`}
            </span>
          </p>
          <p>
            Самый плотный день:{' '}
            <span className="font-medium text-foreground">
              {bestDay ? `${formatShortDay(bestDay.day)} · ${bestDay.total}` : '—'}
            </span>
          </p>
        </div>
      </div>
    </div>
  )
}

type OverviewData = Awaited<ReturnType<typeof api.overview>>

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

  const {
    stats,
    progress,
    due_count,
    strong_categories,
    weak_categories,
    recent,
    activity,
    active_session,
  } = data
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
              Здесь только вопросы, в которых была ошибка. Интервалы помогают закрепить ответ.
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
                ? 'Результаты сохраняются; ошибки можно будет отдельно закрепить.'
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
        <>
          <div className="mt-6 grid grid-cols-2 gap-x-4 gap-y-5 sm:grid-cols-4">
            <Stat value={num(stats.total_attempts)} label="попыток всего" />
            <Stat value={pct === null ? '—' : `${pct}%`} label="верных ответов" />
            <Stat value={num(stats.distinct_questions)} label="разных вопросов" />
            <Stat value={num(due_count)} label="ошибок к закреплению" />
          </div>
          <div className="mt-5 grid grid-cols-2 gap-x-4 gap-y-5 border-t border-border pt-5 sm:grid-cols-4">
            <Stat
              value={
                progress.recent_success_pct == null
                  ? '—'
                  : `${progress.recent_success_pct}%`
              }
              label={`последние ${progress.recent_sample} ответов`}
            />
            <Stat value={num(progress.current_streak)} label="дней подряд" />
            <Stat value={num(progress.active_days_30)} label="активных дней за месяц" />
            <Stat
              value={
                progress.recent_avg_seconds == null
                  ? '—'
                  : `${Math.round(progress.recent_avg_seconds)} с`
              }
              label="среднее время на вопрос"
            />
          </div>
        </>
      )}

      {/* ─── Сильные темы ─────────────────────────────────────── */}
      <Section
        title="Сильные темы"
        action={
          strong_categories.length > 0 ? (
            <Link
              to="/topics"
              className="text-2xs text-amber-ink underline-offset-2 hover:underline"
            >
              все темы
            </Link>
          ) : undefined
        }
      >
        {strong_categories.length === 0 ? (
          <Empty
            title="Пока недостаточно данных"
            hint="Предварительная сильная сторона появляется после двух попыток по теме."
          />
        ) : (
          <>
            <TopicRows items={strong_categories} tone="strong" />
            {strong_categories.some((item) => item.attempts_count < 5) && (
              <p className="mt-2 text-2xs text-muted-foreground">
                Результаты с выборкой меньше пяти вопросов предварительные.
              </p>
            )}
          </>
        )}
      </Section>

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
          <TopicRows items={weak_categories} tone="weak" />
        )}
      </Section>

      {/* ─── Активность ───────────────────────────────────────── */}
      {!untouched && (
        <Section title="Активность и динамика">
          <ProgressHistory activity={activity} />
        </Section>
      )}

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
