import { useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, X, ExternalLink } from 'lucide-react'
import { api, ApiError, type TrainingState } from '@/lib/api'
import { Empty, ErrorState, BlockSkeleton, loadError } from '@/components/States'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { fmtTime, num } from '@/lib/format'

/** Таймер сессии. Отдельный компонент — чтобы тик не перерисовывал вопрос. */
function Timer({ startedAt }: { startedAt: number }) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [])
  return (
    <span className="tabular text-2xs text-muted-foreground">
      {fmtTime((now - startedAt) / 1000)}
    </span>
  )
}

function Summary({ state }: { state: TrainingState }) {
  const navigate = useNavigate()
  const s = state.summary!
  const byCat = Object.entries(s.by_category)

  return (
    <div className="mx-auto max-w-[640px] px-4 py-10 sm:px-6">
      <h1 className="font-serif text-[19px] font-semibold">Тренировка завершена</h1>
      <p className="mt-1 text-xs text-muted-foreground">{s.filters_repr}</p>

      <div className="mt-6 flex items-baseline gap-6 border-y border-border py-5">
        <div>
          <div className="tabular font-serif text-[34px] leading-none font-semibold">
            {s.correct}
            <span className="text-[20px] text-muted-foreground">/{s.total}</span>
          </div>
          <div className="mt-1.5 text-2xs text-muted-foreground">верных ответов</div>
        </div>
        <div>
          <div className="tabular font-serif text-[26px] leading-none font-semibold">
            {s.pct}%
          </div>
          <div className="mt-1.5 text-2xs text-muted-foreground">результат</div>
        </div>
        <div>
          <div className="tabular font-serif text-[26px] leading-none font-semibold">
            {fmtTime(s.avg_time)}
          </div>
          <div className="mt-1.5 text-2xs text-muted-foreground">в среднем</div>
        </div>
      </div>

      {byCat.length > 1 && (
        <section className="mt-6">
          <h2 className="mb-2 text-xs font-medium tracking-wide text-muted-foreground uppercase">
            По темам
          </h2>
          <ul className="divide-y divide-border border-y border-border">
            {byCat.map(([cat, v]) => (
              <li key={cat} className="flex items-center gap-3 py-2 text-xs">
                <span className="min-w-0 flex-1 truncate">{cat}</span>
                <span className="tabular text-muted-foreground">
                  {v.correct}/{v.total}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="mt-6">
        <h2 className="mb-2 text-xs font-medium tracking-wide text-muted-foreground uppercase">
          Разбор
        </h2>
        <ul className="divide-y divide-border border-y border-border">
          {state.results.map((r, i) => (
            <li key={i} className="flex gap-3 py-2.5">
              {r.knew ? (
                <Check className="mt-0.5 size-3.5 shrink-0 text-knew" strokeWidth={2.5} aria-hidden />
              ) : (
                <X className="mt-0.5 size-3.5 shrink-0 text-missed" strokeWidth={2.5} aria-hidden />
              )}
              <div className="min-w-0 flex-1">
                <a
                  href={`/question/${r.question_id}`}
                  className="font-serif text-[13px] font-medium hover:underline"
                >
                  {r.correct_answer}
                </a>
                {r.user_answer && (
                  <p className="mt-0.5 text-2xs text-muted-foreground">
                    ваш ответ: «{r.user_answer}»
                  </p>
                )}
              </div>
              <span className="tabular shrink-0 text-2xs text-muted-foreground">
                {fmtTime(r.time_seconds)}
              </span>
            </li>
          ))}
        </ul>
      </section>

      <p className="mt-5 text-2xs text-muted-foreground">
        Результаты сохранены. Вопросы вернутся на повторение по интервальному расписанию.
      </p>

      <div className="mt-5 flex gap-2">
        <Button onClick={() => navigate('/training')}>Новая тренировка</Button>
        <Button variant="outline" onClick={() => navigate('/')}>
          К обзору
        </Button>
      </div>
    </div>
  )
}

export function Session() {
  const { sessionId } = useParams<{ sessionId: string }>()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const inputRef = useRef<HTMLInputElement>(null)

  const [answer, setAnswer] = useState('')
  const [revealed, setRevealed] = useState(false)
  const [questionStart, setQuestionStart] = useState(() => Date.now())

  const { data: state, isPending, error, fetchStatus, refetch } = useQuery({
    queryKey: ['training', sessionId],
    queryFn: () => api.trainingState(sessionId!),
    enabled: !!sessionId,
    // Сессия живёт на сервере; лишние перезапросы только сбивают таймер.
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  })
  const err = loadError(error, fetchStatus)

  const apply = (s: TrainingState) => {
    qc.setQueryData(['training', sessionId], s)
    qc.invalidateQueries({ queryKey: ['overview'] })
  }

  /** Сессия пропала на сервере — показываем это через основной запрос. */
  const onMutationError = (e: unknown) => {
    if (e instanceof ApiError && e.status === 404) refetch()
  }

  const reveal = useMutation({
    mutationFn: () => api.reveal(sessionId!, answer),
    onSuccess: (s) => {
      apply(s)
      setRevealed(true)
    },
    onError: onMutationError,
  })

  const grade = useMutation({
    mutationFn: (knew: boolean) => api.grade(sessionId!, knew),
    onSuccess: (s) => {
      apply(s)
      setRevealed(false)
      setAnswer('')
      setQuestionStart(Date.now())
      if (!s.finished) setTimeout(() => inputRef.current?.focus(), 0)
    },
    onError: onMutationError,
  })

  const abort = useMutation({
    mutationFn: () => api.abort(sessionId!),
    onSuccess: apply,
    onError: onMutationError,
  })

  // Управление с клавиатуры: Enter — показать ответ, 1/2 — оценка.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!state || state.finished) return
      if (e.metaKey || e.ctrlKey || e.altKey) return

      if (!revealed) {
        if (e.key === 'Enter') {
          e.preventDefault()
          reveal.mutate()
        }
        return
      }
      // Ответ раскрыт — поле ввода уже не в фокусе.
      if (e.key === '1' || e.key === 'ArrowLeft') {
        e.preventDefault()
        grade.mutate(true)
      } else if (e.key === '2' || e.key === 'ArrowRight') {
        e.preventDefault()
        grade.mutate(false)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [state, revealed, reveal, grade])

  useEffect(() => {
    if (state && !state.finished && !revealed) inputRef.current?.focus()
  }, [state?.index, revealed, state])

  if (err) {
    // Сессия живёт в памяти сервера: после перезапуска её нет.
    // Уже оценённые вопросы при этом сохранены — говорим об этом прямо.
    const lost = err instanceof ApiError && err.status === 404
    return (
      <div className="mx-auto max-w-[640px] px-4 py-10">
        {lost ? (
          <Empty
            title="Сессия больше не активна"
            hint="Похоже, сервер перезапустился. Ответы, которые вы уже оценили, сохранены — их видно в обзоре и в истории вопроса."
            action={
              <Button size="sm" onClick={() => navigate('/training')}>
                Начать заново
              </Button>
            }
          />
        ) : (
          <>
            <ErrorState error={err} onRetry={() => refetch()} />
            <Button
              variant="outline"
              size="sm"
              className="mt-4"
              onClick={() => navigate('/training')}
            >
              К настройке тренировки
            </Button>
          </>
        )}
      </div>
    )
  }

  if (isPending || !state) {
    return (
      <div className="mx-auto max-w-[640px] px-4 py-10">
        <BlockSkeleton className="h-40" />
      </div>
    )
  }

  if (state.finished) return <Summary state={state} />

  const q = state.question!
  const progress = (state.index / state.total) * 100
  const razdatkaPic = q.razdatka_pic
    ? q.razdatka_pic.startsWith('http')
      ? q.razdatka_pic
      : `https://gotquestions.online${q.razdatka_pic}`
    : null

  return (
    <div className="flex min-h-screen flex-col">
      {/* ─── Прогресс сессии: тонкая линия, без «геймификации» ──── */}
      <div className="h-0.5 w-full shrink-0 bg-paper-sunk" role="presentation">
        <div
          className="h-full bg-amber transition-[width] duration-200"
          style={{ width: `${progress}%` }}
        />
      </div>

      <header className="flex shrink-0 items-center gap-3 px-4 py-3 sm:px-6">
        <span className="tabular text-2xs font-medium">
          {state.index + 1}
          <span className="text-muted-foreground">/{state.total}</span>
        </span>
        <span className="truncate text-2xs text-muted-foreground">{state.filters_repr}</span>
        <div className="ml-auto flex items-center gap-3">
          <Timer startedAt={questionStart} />
          <Button
            variant="ghost"
            size="sm"
            onClick={() => abort.mutate()}
            disabled={abort.isPending}
          >
            Завершить
          </Button>
        </div>
      </header>

      {/* ─── Вопрос ────────────────────────────────────────────── */}
      <main className="flex-1 px-4 pb-8 sm:px-6">
        <div className="mx-auto max-w-[680px]">
          {/* Тема — подсказка: «Кино и театр» сужает поиск вдвое, а в игре
              вам её не скажут. Показываем только после ответа, там она нужна
              для разбора. Беручесть тоже прячем: «взяли 100%» до ответа —
              это указание не думать. */}
          {revealed && (q.category || q.question_difficulty !== null) && (
            <p className="mb-4 flex flex-wrap items-center gap-x-2.5 text-2xs">
              {q.category && (
                <span className="text-amber-ink">
                  {q.category}
                  {q.subcategory && (
                    <span className="text-muted-foreground"> · {q.subcategory}</span>
                  )}
                </span>
              )}
              {q.question_difficulty !== null && q.question_difficulty !== undefined && (
                <span className="tabular text-muted-foreground">
                  взяли {Math.round((10 - q.question_difficulty) * 10)}%
                </span>
              )}
            </p>
          )}

          {(q.razdatka_text || razdatkaPic) && (
            <div className="mb-5 rounded-lg border border-amber/40 bg-amber-wash/40 px-4 py-3">
              <p className="mb-1.5 text-2xs font-medium tracking-wide text-amber-ink uppercase">
                Раздатка
              </p>
              {q.razdatka_text && (
                <p className="prose-question text-[15px] whitespace-pre-wrap">
                  {q.razdatka_text}
                </p>
              )}
              {razdatkaPic && (
                <img
                  src={razdatkaPic}
                  alt="Раздаточный материал"
                  className="mt-2 max-h-72 rounded-md border border-border"
                />
              )}
            </div>
          )}

          <p className="question-text whitespace-pre-wrap">{q.text}</p>

          {/* ─── Ввод / ответ ─────────────────────────────────── */}
          {!revealed ? (
            <div className="mt-8">
              {/* На узком экране кнопка уходит под поле: в строке ей тесно. */}
              <div className="flex flex-col gap-2 sm:flex-row">
                <Input
                  ref={inputRef}
                  value={answer}
                  onChange={(e) => setAnswer(e.target.value)}
                  placeholder="Ваша версия (необязательно)…"
                  aria-label="Ваш ответ"
                  className="h-10 sm:h-9"
                />
                <Button
                  onClick={() => reveal.mutate()}
                  disabled={reveal.isPending}
                  className="h-10 shrink-0 sm:h-9"
                >
                  Показать ответ
                </Button>
              </div>
              {/* Подсказка про клавиши бессмысленна без клавиатуры. */}
              <p className="mt-2 hidden text-2xs text-muted-foreground sm:block">
                <span className="kbd">Enter</span> — показать ответ
              </p>
            </div>
          ) : (
            <div className="mt-8 animate-reveal">
              {state.results.length >= 0 && answer && (
                <p className="mb-3 text-xs text-muted-foreground">
                  Ваша версия: «{answer}»
                </p>
              )}

              <div className="border-l-2 border-knew pl-4">
                <p className="mb-1 text-2xs font-medium tracking-wide text-knew uppercase">
                  Ответ
                </p>
                <p className="prose-question text-[17px] font-medium whitespace-pre-wrap">
                  {q.answer}
                </p>
              </div>

              {q.zachet && (
                <div className="mt-3 border-l-2 border-border pl-4">
                  <p className="mb-1 text-2xs font-medium tracking-wide text-muted-foreground uppercase">
                    Зачёт
                  </p>
                  <p className="prose-question text-[15px] whitespace-pre-wrap">{q.zachet}</p>
                </div>
              )}

              {q.comment && (
                <div className="mt-4">
                  <p className="mb-1 text-2xs font-medium tracking-wide text-muted-foreground uppercase">
                    Комментарий
                  </p>
                  <p className="prose-question text-[15px] whitespace-pre-wrap text-foreground/90">
                    {q.comment}
                  </p>
                </div>
              )}

              {q.source && (
                <p className="mt-4 text-2xs text-muted-foreground">
                  Источник: <span className="break-words">{q.source}</span>
                </p>
              )}

              <div className="mt-4 flex items-center gap-3 text-2xs">
                <a
                  href={`/question/${q.id}`}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-1 text-muted-foreground hover:text-amber-ink"
                >
                  Карточка вопроса
                  <ExternalLink className="size-3" aria-hidden />
                </a>
              </div>

              {/* Оценка. Движок хранит булево «знал», поэтому две кнопки. */}
              <div className="mt-7 flex flex-col gap-2 sm:flex-row">
                <Button
                  onClick={() => grade.mutate(true)}
                  disabled={grade.isPending}
                  className="h-10 flex-1 border-knew/30 bg-knew-wash text-knew hover:bg-knew-wash/70"
                >
                  <Check className="size-4" aria-hidden />
                  Знал
                  <span className="kbd ml-1.5 hidden sm:inline-flex">1</span>
                </Button>
                <Button
                  onClick={() => grade.mutate(false)}
                  disabled={grade.isPending}
                  className="h-10 flex-1 border-missed/30 bg-missed-wash text-missed hover:bg-missed-wash/70"
                >
                  <X className="size-4" aria-hidden />
                  Не знал
                  <span className="kbd ml-1.5 hidden sm:inline-flex">2</span>
                </Button>
              </div>
            </div>
          )}

          {/* ─── Счёт сессии ──────────────────────────────────── */}
          {state.results.length > 0 && (
            <p className="tabular mt-8 text-2xs text-muted-foreground">
              Верно {state.results.filter((r) => r.knew).length} из {state.results.length} ·
              осталось {num(state.total - state.index)}
            </p>
          )}
        </div>
      </main>
    </div>
  )
}
