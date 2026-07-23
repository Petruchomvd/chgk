import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useMutation, useQuery } from '@tanstack/react-query'
import { GraduationCap, Check, BookOpen, Dumbbell } from 'lucide-react'
import { api, type CanonItem } from '@/lib/api'
import { Page } from '@/components/AppShell'
import { BlockSkeleton } from '@/components/States'

const LEARNED_KEY = 'study-learned-v1'

function loadLearned(): Set<string> {
  try {
    return new Set(JSON.parse(localStorage.getItem(LEARNED_KEY) || '[]'))
  } catch {
    return new Set()
  }
}

const pct = (x: number | null) => (x == null ? '—' : `${Math.round(x * 100)}%`)

export function Study() {
  const navigate = useNavigate()
  const [learned, setLearned] = useState<Set<string>>(loadLearned)
  const [catId, setCatId] = useState<number | null>(null)
  const [selected, setSelected] = useState<CanonItem | null>(null)

  const trainFact = useMutation({
    mutationFn: (answer: string) =>
      api.startTraining({ mode: 'study_fact', answer, count: 10 }),
    onSuccess: (s) => navigate(`/training/${s.session_id}`),
  })

  const toggleLearned = (key: string) => {
    setLearned((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      localStorage.setItem(LEARNED_KEY, JSON.stringify([...next]))
      return next
    })
  }

  const { data: meta } = useQuery({ queryKey: ['meta'], queryFn: api.meta })
  const { data: weakTopics } = useQuery({
    queryKey: ['weak-topics'],
    queryFn: api.weakTopics,
  })

  // Слабые темы команды (n>=5, дефицит) — их учить в первую очередь.
  const weakNames = useMemo(() => {
    const set = new Set<string>()
    for (const c of weakTopics?.categories ?? []) {
      if (c.weak) set.add(c.category)
    }
    return set
  }, [weakTopics])

  const weakFirst = useMemo(() => {
    const categories = meta?.categories ?? []
    const weak = categories.filter((c) => weakNames.has(c.name_ru))
    const rest = categories.filter((c) => !weakNames.has(c.name_ru))
    return [...weak, ...rest]
  }, [meta?.categories, weakNames])

  // По умолчанию — самая слабая тема.
  useEffect(() => {
    if (catId == null && weakFirst.length && weakNames.size) {
      const firstWeak = weakFirst.find((c) => weakNames.has(c.name_ru))
      if (firstWeak) setCatId(firstWeak.id)
    }
  }, [catId, weakFirst, weakNames])

  const { data: canon, isFetching: loadingCanon } = useQuery({
    queryKey: ['study-canon', catId],
    queryFn: () => api.studyCanon(catId, 48),
  })

  const { data: fact, isFetching: loadingFact } = useQuery({
    queryKey: ['study-fact', selected?.key],
    queryFn: () => api.studyFact(selected!.answer),
    enabled: !!selected,
  })

  const canonItems = canon?.items ?? []
  const learnedInView = canonItems.filter((i) => learned.has(i.key)).length

  return (
    <Page>
      <div className="mb-1 flex items-center gap-2">
        <GraduationCap className="size-5 text-amber-ink" aria-hidden />
        <h1 className="text-lg font-semibold">Учить</h1>
      </div>
      <p className="mb-4 text-xs text-muted-foreground">
        В ЧГК десятки ответов повторяются из турнира в турнир, и каждый спрашивают
        с разных сторон. Знать канон и его «зацепки» — значит брать эти вопросы.
        Ниже — что учить по темам (слабые темы команды отмечены&nbsp;<span className="text-amber-ink">●</span>),
        а по каждому ответу — все углы вопроса с готовым разбором.
      </p>
      {meta && !meta.features.fact_cards && (
        <div className="mb-4 rounded-lg border border-amber/40 bg-amber-wash/40 px-3.5 py-3 text-xs">
          Подготовленные карточки фактов пока не подключены. Примеры вопросов и
          редакторские комментарии доступны, но краткого конспекта нет.
        </div>
      )}

      {/* Выбор темы */}
      <div className="mb-4 flex flex-wrap gap-1.5">
        <Chip active={catId == null} onClick={() => { setCatId(null); setSelected(null) }}>
          Весь корпус
        </Chip>
        {weakFirst.map((c) => (
          <Chip
            key={c.id}
            active={catId === c.id}
            weak={weakNames.has(c.name_ru)}
            onClick={() => { setCatId(c.id); setSelected(null) }}
          >
            {weakNames.has(c.name_ru) && <span className="text-amber-ink">● </span>}
            {c.name_ru}
          </Chip>
        ))}
      </div>

      <div className="grid gap-5 md:grid-cols-[minmax(0,20rem)_1fr]">
        {/* Канон темы */}
        <div>
          <div className="mb-2 flex items-center justify-between text-2xs uppercase tracking-wide text-muted-foreground">
            <span>Что нужно знать</span>
            {canonItems.length > 0 && (
              <span className="tabular">{learnedInView}/{canonItems.length} изучено</span>
            )}
          </div>
          {loadingCanon ? (
            <BlockSkeleton className="h-64" />
          ) : (
            <ul className="divide-y divide-border border-y border-border">
              {canonItems.map((it) => {
                const done = learned.has(it.key)
                const active = selected?.key === it.key
                return (
                  <li key={it.key}>
                    <button
                      onClick={() => setSelected(it)}
                      className={`flex w-full items-center gap-2 py-2 pr-1 text-left text-xs transition ${
                        active ? 'text-amber-ink' : 'hover:text-amber-ink'
                      }`}
                    >
                      <span
                        className={`grid size-4 shrink-0 place-items-center rounded-full border ${
                          done ? 'border-emerald-500 bg-emerald-500 text-white' : 'border-ink-line'
                        }`}
                      >
                        {done && <Check className="size-2.5" aria-hidden />}
                      </span>
                      <span className={`flex-1 truncate ${done ? 'text-muted-foreground line-through' : ''}`}>
                        {it.answer}
                      </span>
                      <span className="tabular shrink-0 rounded bg-amber-wash/60 px-1.5 py-0.5 text-2xs text-amber-ink">
                        {it.count}
                      </span>
                    </button>
                  </li>
                )
              })}
            </ul>
          )}
        </div>

        {/* Досье факта */}
        <div>
          {!selected && (
            <div className="flex h-full min-h-40 flex-col items-center justify-center rounded-lg border border-dashed border-border text-center text-xs text-muted-foreground">
              <BookOpen className="mb-2 size-6 opacity-40" aria-hidden />
              Выберите ответ слева — покажу все вопросы про него<br />и разбор каждого.
            </div>
          )}
          {selected && (
            <div>
              <div className="mb-3 flex flex-wrap items-center gap-2">
                <h2 className="text-base font-semibold">{fact?.answer ?? selected.answer}</h2>
                {fact && (
                  <span className="text-2xs text-muted-foreground">
                    {fact.total} вопрос(ов) в корпусе
                  </span>
                )}
                <div className="ml-auto flex items-center gap-2">
                  <button
                    onClick={() => trainFact.mutate(selected.answer)}
                    disabled={trainFact.isPending}
                    className="flex items-center gap-1 rounded-md bg-amber-ink px-2.5 py-1 text-2xs font-medium text-white transition hover:opacity-90 disabled:opacity-60"
                  >
                    <Dumbbell className="size-3" aria-hidden />
                    тренировать факт
                  </button>
                  <button
                    onClick={() => toggleLearned(selected.key)}
                    className={`flex items-center gap-1 rounded-md px-2.5 py-1 text-2xs font-medium transition ${
                      learned.has(selected.key)
                        ? 'bg-emerald-500 text-white'
                        : 'border border-ink-line hover:border-amber-ink'
                    }`}
                  >
                    <Check className="size-3" aria-hidden />
                    {learned.has(selected.key) ? 'изучил' : 'отметить изученным'}
                  </button>
                </div>
              </div>

              {fact?.card && (fact.card.core || fact.card.hooks.length > 0) && (
                <div className="mb-4 rounded-lg border border-amber-ink/25 bg-amber-wash/30 p-3">
                  {fact.card.core && (
                    <p className="mb-2 text-xs leading-relaxed font-medium">{fact.card.core}</p>
                  )}
                  <ul className="space-y-1.5">
                    {fact.card.hooks.map((h, i) => (
                      <li key={i} className="text-xs leading-relaxed">
                        <span className="mr-1.5 text-amber-ink">•</span>
                        {h.fact}
                        {h.angle && (
                          <span className="ml-1 text-2xs text-muted-foreground">— {h.angle}</span>
                        )}
                      </li>
                    ))}
                  </ul>
                  <p className="mt-2 text-2xs text-muted-foreground">
                    Карточка заранее подготовлена ИИ и сверена с источниками. При
                    открытии страницы новые запросы к модели не выполняются.
                  </p>
                </div>
              )}

              {fact && fact.angles.length > 0 && (
                <div className="mb-1.5 text-2xs uppercase tracking-wide text-muted-foreground">
                  Примеры вопросов
                </div>
              )}
              {loadingFact ? (
                <BlockSkeleton className="h-64" />
              ) : (
                <ol className="space-y-3">
                  {(fact?.angles ?? []).map((a) => (
                    <li key={a.id} className="rounded-lg border border-border p-3">
                      <div className="mb-1 flex items-center gap-2 text-2xs text-muted-foreground">
                        <span className="tabular rounded bg-amber-wash/60 px-1.5 py-0.5 text-amber-ink">
                          поле {pct(a.take_rate)}
                        </span>
                        {a.pack_title && (
                          <span className="truncate">
                            {a.pack_title}
                            {a.year && ` · ${a.year}`}
                          </span>
                        )}
                      </div>
                      <p className="text-xs leading-relaxed">{a.text}</p>
                      {a.comment && (
                        <p className="mt-1.5 border-l-2 border-amber-ink/30 pl-2 text-2xs leading-relaxed text-muted-foreground">
                          {a.comment}
                        </p>
                      )}
                      <Link
                        to={`/question/${a.id}`}
                        className="mt-1.5 inline-block text-2xs text-amber-ink hover:underline"
                      >
                        открыть вопрос →
                      </Link>
                    </li>
                  ))}
                </ol>
              )}
            </div>
          )}
        </div>
      </div>
    </Page>
  )
}

function Chip({
  active,
  weak,
  onClick,
  children,
}: {
  active: boolean
  weak?: boolean
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <button
      onClick={onClick}
      className={`rounded-full border px-2.5 py-1 text-2xs transition ${
        active
          ? 'border-amber-ink bg-amber-wash/60 text-amber-ink'
          : weak
            ? 'border-amber-ink/30 hover:border-amber-ink'
            : 'border-border text-muted-foreground hover:border-amber-ink hover:text-amber-ink'
      }`}
    >
      {children}
    </button>
  )
}
