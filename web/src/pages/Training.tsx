import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQuery } from '@tanstack/react-query'
import { ChevronDown, Search, SlidersHorizontal } from 'lucide-react'
import { api } from '@/lib/api'
import { Page, PageHeader } from '@/components/AppShell'
import { ErrorState } from '@/components/States'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Checkbox } from '@/components/ui/checkbox'
import { Label } from '@/components/ui/label'
import { num, questionsWord } from '@/lib/format'
import { cn } from '@/lib/utils'

type Mode = 'random' | 'marked' | 'weak' | 'category' | 'tournament' | 'followup'

const MODES: { value: Mode; label: string; hint: string }[] = [
  { value: 'random', label: 'Случайные', hint: 'Из всей базы вопросов' },
  { value: 'marked', label: 'Только размеченные', hint: 'Вопросы с определённой темой' },
  { value: 'weak', label: 'Слабые темы', hint: 'Новые вопросы по измеренным провалам' },
  { value: 'followup', label: 'Работа над ошибками', hint: 'Другие вопросы с теми же ответами' },
  { value: 'category', label: 'Выбрать темы', hint: 'Одна или несколько конкретных тем' },
  { value: 'tournament', label: 'Турнир', hint: 'Вопросы одного пакета подряд' },
]

const QUICK_MODES = (['random', 'marked', 'followup', 'weak'] as Mode[]).map(
  (value) => MODES.find((mode) => mode.value === value)!,
)
const EXTRA_MODES = MODES.filter((mode) =>
  ['category', 'tournament'].includes(mode.value),
)

const COUNTS = [6, 12, 24, 36]

// Приём — вторая ось разметки (см. scripts/detect_techniques.py).
const TECHS = [
  { value: 'any', label: 'Любой' },
  { value: 'замена', label: 'Замены' },
  { value: 'пропуск', label: 'Пропуски' },
  { value: 'раздатка', label: 'Раздатки' },
  { value: 'блиц', label: 'Блицы' },
  { value: 'цитата', label: 'Цитаты' },
  { value: 'чистый', label: 'Чистое знание' },
]

// Слои беручести. Пороги те же, что в scripts/team_gap.py, — иначе «средние»
// в приложении и «средние» в отчёте о провалах означали бы разное.
// q.difficulty = 10 × (1 − доля взявших команд), поэтому границы перевёрнуты.
type Layer = { value: string; label: string; hint: string; range: [number, number] | null }

const LAYERS: Layer[] = [
  { value: 'any', label: 'Любая', hint: 'вся база', range: null },
  { value: 'easy', label: 'Берут все', hint: '≥85%', range: [0, 1.5] },
  { value: 'medium-easy', label: 'Лёгкие', hint: '70–85%', range: [1.5, 3.0] },
  { value: 'medium', label: 'Средние', hint: '40–70%', range: [3.0, 6.0] },
  { value: 'hard', label: 'Трудные', hint: '15–40%', range: [6.0, 8.5] },
  { value: 'brutal', label: 'Гробы', hint: '<15%', range: [8.5, 10] },
]

export function Training() {
  const navigate = useNavigate()
  const [mode, setMode] = useState<Mode>('random')
  const [count, setCount] = useState(12)
  const [categoryIds, setCategoryIds] = useState<number[]>([])
  const [packId, setPackId] = useState<number | null>(null)
  const [tournamentSearch, setTournamentSearch] = useState('')
  const [tournamentYear, setTournamentYear] = useState<number | null>(null)
  const [layer, setLayer] = useState<string>('any')
  const [tech, setTech] = useState<string>('any')
  const [advanced, setAdvanced] = useState(false)

  const activeLayer = LAYERS.find((l) => l.value === layer) ?? LAYERS[0]

  const { data: meta } = useQuery({ queryKey: ['meta'], queryFn: api.meta, staleTime: Infinity })
  const { data: weak } = useQuery({
    queryKey: ['weak-topics'],
    queryFn: api.weakTopics,
    staleTime: Infinity,
  })
  const { data: tournaments } = useQuery({
    queryKey: ['tournaments', tournamentSearch, tournamentYear],
    queryFn: () =>
      api.tournaments(tournamentSearch, tournamentYear, tournamentYear ? 500 : 80),
    enabled: mode === 'tournament',
  })
  const selectedTournament = tournaments?.items.find((t) => t.id === packId) ?? null
  const countOptions =
    mode === 'tournament' && selectedTournament
      ? [
          ...COUNTS.filter((value) => value < selectedTournament.questions_count),
          selectedTournament.questions_count,
        ]
      : COUNTS

  const weakIds = (weak?.categories ?? [])
    .filter((c) => c.weak && c.category_id !== null)
    .map((c) => c.category_id as number)

  // В режиме «Турнир» вопросы идут пакетом подряд — отбирать их по сложности
  // бессмысленно, это уже не турнир.
  // Слой сложности не применим к турниру (пакет играется как есть) и к работе
  // над ошибками (сложность там задаёт сам провал, а не фильтр).
  const layerApplies = mode !== 'tournament' && mode !== 'followup'

  const start = useMutation({
    mutationFn: () =>
      api.startTraining({
        mode,
        count,
        category_ids:
          mode === 'category' ? categoryIds : mode === 'weak' ? weakIds : undefined,
        pack_id: mode === 'tournament' ? packId : undefined,
        difficulty_min: layerApplies ? activeLayer.range?.[0] : undefined,
        difficulty_max: layerApplies ? activeLayer.range?.[1] : undefined,
        technique: layerApplies && tech !== 'any' ? tech : undefined,
      }),
    onSuccess: (s) => navigate(`/training/${s.session_id}`),
  })

  const canStart =
    (mode === 'category' && categoryIds.length > 0) ||
    (mode === 'tournament' && packId !== null) ||
    (mode === 'weak' && weakIds.length > 0) ||
    mode === 'random' ||
    mode === 'marked' ||
    mode === 'followup'

  const selectMode = (value: Mode, collapse = false) => {
    setMode(value)
    if (value !== 'tournament' && !COUNTS.includes(count)) {
      setCount(12)
    }
    if (collapse) {
      setAdvanced(false)
      setLayer('any')
      setTech('any')
    }
    start.reset()
  }

  return (
    <Page>
      <PageHeader title="Тренировка" meta="Выберите цель и начинайте" />

      {/* Частые сценарии остаются на первом экране. Редкие фильтры
          раскрываются отдельно и не мешают быстро начать тренировку. */}
      <fieldset>
        <legend className="mb-2 text-xs font-medium tracking-wide text-muted-foreground uppercase">
          Цель тренировки
        </legend>
        <div className="grid gap-2 sm:grid-cols-2">
          {QUICK_MODES.map((m) => (
            <button
              key={m.value}
              type="button"
              onClick={() => selectMode(m.value, true)}
              aria-pressed={mode === m.value}
              className={cn(
                'rounded-lg border px-3.5 py-3 text-left transition-colors',
                mode === m.value
                  ? 'border-amber bg-amber-wash/50'
                  : 'border-border bg-paper-raised hover:border-amber-soft',
              )}
            >
              <div className="text-[13px] font-medium">{m.label}</div>
              <div className="mt-0.5 text-2xs leading-snug text-muted-foreground">{m.hint}</div>
            </button>
          ))}
        </div>
      </fieldset>

      <button
        type="button"
        onClick={() => setAdvanced((value) => !value)}
        aria-expanded={advanced}
        className="mt-4 flex min-h-10 items-center gap-2 rounded-md text-xs font-medium text-muted-foreground transition-colors hover:text-foreground"
      >
        <SlidersHorizontal className="size-3.5" aria-hidden />
        Настроить тренировку
        <ChevronDown
          className={cn('size-3.5 transition-transform', advanced && 'rotate-180')}
          aria-hidden
        />
      </button>

      {advanced && (
        <fieldset className="mt-2 rounded-lg border border-border bg-paper-raised p-3.5">
          <legend className="px-1 text-2xs font-medium tracking-wide text-muted-foreground uppercase">
            Другой формат
          </legend>
          <div className="grid gap-2 sm:grid-cols-2">
            {EXTRA_MODES.map((m) => (
              <button
                key={m.value}
                type="button"
                onClick={() => selectMode(m.value)}
                aria-pressed={mode === m.value}
                className={cn(
                  'rounded-lg border px-3.5 py-3 text-left transition-colors',
                  mode === m.value
                    ? 'border-amber bg-amber-wash/50'
                    : 'border-border bg-paper hover:border-amber-soft',
                )}
              >
                <div className="text-[13px] font-medium">{m.label}</div>
                <div className="mt-0.5 text-2xs leading-snug text-muted-foreground">{m.hint}</div>
              </button>
            ))}
          </div>
        </fieldset>
      )}

      {/* ─── Слабые темы ───────────────────────────────────────── */}
      {advanced && mode === 'weak' && (
        <fieldset className="mt-6">
          <legend className="mb-2 text-xs font-medium tracking-wide text-muted-foreground uppercase">
            По каким темам
          </legend>
          {!weak?.available ? (
            <p className="rounded-lg border border-dashed border-border px-3.5 py-4 text-xs leading-relaxed text-muted-foreground">
              Нет измерений. Слабые темы считает <code>scripts/team_gap.py</code> по
              реально сыгранным турнирам — он сравнивает ваши взятия с полем.
            </p>
          ) : weakIds.length === 0 ? (
            <p className="rounded-lg border border-dashed border-border px-3.5 py-4 text-xs leading-relaxed text-muted-foreground">
              По измерениям ни одна тема не проседает заметно сильнее остальных.
            </p>
          ) : (
            <>
              <ul className="divide-y divide-border rounded-lg border border-border bg-paper-raised">
                {weak.categories
                  .filter((c) => c.weak)
                  .map((c) => (
                    <li
                      key={c.category}
                      className="flex items-baseline gap-3 px-3.5 py-2 text-xs"
                    >
                      <span className="flex-1">{c.category}</span>
                      <span className="tabular text-2xs text-muted-foreground">
                        взято {c.took} из {c.questions}
                      </span>
                      <span className="tabular w-12 text-right text-2xs text-missed">
                        {c.deficit.toFixed(1)}
                      </span>
                    </li>
                  ))}
              </ul>
              <p className="mt-2 text-2xs leading-relaxed text-muted-foreground">
                Измерено на {num(weak.questions_total ?? 0)}{' '}
                {questionsWord(weak.questions_total ?? 0)} реальных турниров: это темы,
                где вы недобираете относительно поля сильнее своего среднего. Последняя
                колонка — сколько ответов недобрано. Показываются только новые вопросы:
                повторять уже виденный вопрос ЧГК смысла нет, его не спросят второй раз.
              </p>
            </>
          )}
        </fieldset>
      )}

      {/* ─── Темы ──────────────────────────────────────────────── */}
      {advanced && mode === 'category' && (
        <fieldset className="mt-6">
          <legend className="mb-2 flex w-full items-baseline justify-between text-xs font-medium tracking-wide text-muted-foreground uppercase">
            <span>Темы</span>
            <button
              type="button"
              className="text-2xs normal-case hover:text-amber-ink"
              onClick={() => {
                setCategoryIds(
                  categoryIds.length === meta?.categories.length
                    ? []
                    : (meta?.categories.map((c) => c.id) ?? []),
                )
                start.reset()
              }}
            >
              {categoryIds.length === meta?.categories.length ? 'снять все' : 'выбрать все'}
            </button>
          </legend>
          <div className="grid gap-x-4 gap-y-1.5 rounded-lg border border-border bg-paper-raised p-3.5 sm:grid-cols-2">
            {meta?.categories.map((c) => (
              <div key={c.id} className="flex items-center gap-2">
                <Checkbox
                  id={`cat-${c.id}`}
                  checked={categoryIds.includes(c.id)}
                  onCheckedChange={(v) => {
                    setCategoryIds((prev) =>
                      v ? [...prev, c.id] : prev.filter((x) => x !== c.id),
                    )
                    start.reset()
                  }}
                />
                <Label htmlFor={`cat-${c.id}`} className="cursor-pointer text-xs font-normal">
                  {c.name_ru}
                </Label>
              </div>
            ))}
          </div>
        </fieldset>
      )}

      {/* ─── Турнир ────────────────────────────────────────────── */}
      {advanced && mode === 'tournament' && (
        <fieldset className="mt-6">
          <legend className="mb-2 text-xs font-medium tracking-wide text-muted-foreground uppercase">
            Турнир
          </legend>
          <div className="relative mb-2">
            <Search
              className="pointer-events-none absolute top-1/2 left-3 size-3.5 -translate-y-1/2 text-muted-foreground"
              aria-hidden
            />
            <Input
              value={tournamentSearch}
              onChange={(e) => {
                setTournamentSearch(e.target.value)
                setPackId(null)
                if (!COUNTS.includes(count)) {
                  setCount(12)
                }
                start.reset()
              }}
              placeholder="Название турнира…"
              aria-label="Поиск турнира"
              className="h-9 pl-9"
            />
          </div>
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <label htmlFor="tournament-year" className="text-2xs text-muted-foreground">
              Год
            </label>
            <select
              id="tournament-year"
              value={tournamentYear ?? ''}
              onChange={(event) => {
                setTournamentYear(event.target.value ? Number(event.target.value) : null)
                setPackId(null)
                if (!COUNTS.includes(count)) {
                  setCount(12)
                }
                start.reset()
              }}
              className="h-8 rounded-md border border-border bg-paper-raised px-2 text-xs"
            >
              <option value="">Последние турниры</option>
              {(tournaments?.years ?? []).map((year) => (
                <option key={year} value={year}>
                  {year}
                </option>
              ))}
            </select>
            <span className="text-2xs text-muted-foreground">
              {tournaments
                ? `${num(tournaments.total)} турниров в списке`
                : 'Загружаем список…'}
            </span>
          </div>
          <ul className="max-h-64 divide-y divide-border overflow-y-auto rounded-lg border border-border bg-paper-raised">
            {tournaments?.items.length === 0 ? (
              <li className="px-3.5 py-4 text-xs text-muted-foreground">Турниры не найдены</li>
            ) : (
              tournaments?.items.map((t) => (
                <li key={t.id}>
                  <button
                    type="button"
                    onClick={() => {
                      setPackId(t.id)
                      setCount(t.questions_count)
                      start.reset()
                    }}
                    aria-pressed={packId === t.id}
                    className={cn(
                      'flex w-full items-center gap-3 px-3.5 py-2 text-left transition-colors',
                      packId === t.id ? 'bg-amber-wash/60' : 'hover:bg-amber-wash/30',
                    )}
                  >
                    <span className="min-w-0 flex-1 truncate text-xs">
                      {t.title}
                      {t.year && (
                        <span className="ml-1.5 text-2xs text-muted-foreground">
                          {t.year}
                        </span>
                      )}
                    </span>
                    <span className="tabular shrink-0 text-2xs text-muted-foreground">
                      {t.questions_count} {questionsWord(t.questions_count)}
                    </span>
                  </button>
                </li>
              ))
            )}
          </ul>
        </fieldset>
      )}

      {/* ─── Беручесть ─────────────────────────────────────────── */}
      {advanced && layerApplies && (
        <fieldset className="mt-6">
          <legend className="mb-2 text-xs font-medium tracking-wide text-muted-foreground uppercase">
            Насколько трудные
          </legend>
          <div className="flex flex-wrap gap-1.5">
            {LAYERS.map((l) => (
              <button
                key={l.value}
                type="button"
              onClick={() => {
                setLayer(l.value)
                start.reset()
              }}
                aria-pressed={layer === l.value}
                className={cn(
                  'rounded-md border px-3 py-1.5 text-left transition-colors',
                  layer === l.value
                    ? 'border-amber bg-amber-wash/60'
                    : 'border-border bg-paper-raised hover:border-amber-soft',
                )}
              >
                <span className="text-xs font-medium">{l.label}</span>
                <span className="tabular ml-1.5 text-2xs text-muted-foreground">{l.hint}</span>
              </button>
            ))}
          </div>
          <p className="mt-2 text-2xs leading-relaxed text-muted-foreground">
            Доля команд, взявших вопрос на турнире. Известна у{' '}
            {num(meta?.with_difficulty ?? 0)} {questionsWord(meta?.with_difficulty ?? 0)} —
            при выборе слоя остальные не участвуют.
          </p>
        </fieldset>
      )}

      {/* ─── Объём ─────────────────────────────────────────────── */}
      <fieldset className="mt-6">
        <legend className="mb-2 text-xs font-medium tracking-wide text-muted-foreground uppercase">
          Сколько вопросов
        </legend>
        <div className="flex gap-1.5">
          {countOptions.map((c) => (
            <button
              key={c}
              type="button"
              onClick={() => {
                setCount(c)
                start.reset()
              }}
              aria-pressed={count === c}
              className={cn(
                'tabular h-8 min-w-14 rounded-md border px-2 text-xs transition-colors',
                count === c
                  ? 'border-amber bg-amber-wash/60 font-medium'
                  : 'border-border bg-paper-raised hover:border-amber-soft',
              )}
            >
              {mode === 'tournament' && selectedTournament?.questions_count === c
                ? `Все ${c}`
                : c}
            </button>
          ))}
        </div>

        {advanced && layerApplies && (
          <>
            <h2 className="mt-5 mb-2 text-2xs font-medium tracking-wide text-muted-foreground uppercase">
              Приём вопроса
            </h2>
            <div className="flex flex-wrap gap-1.5">
              {TECHS.map((t) => (
                <button
                  key={t.value}
                  aria-pressed={tech === t.value}
                  onClick={() => {
                    setTech(t.value)
                    start.reset()
                  }}
                  className={cn(
                    'rounded-md border px-2.5 py-1.5 text-xs',
                    tech === t.value
                      ? 'border-amber bg-amber-wash text-amber-ink'
                      : 'border-border text-muted-foreground hover:text-foreground',
                  )}
                >
                  {t.label}
                </button>
              ))}
            </div>
          </>
        )}
      </fieldset>

      {start.error && (
        <div className="mt-5">
          <ErrorState error={start.error} />
        </div>
      )}

      <div className="mt-7 flex flex-wrap items-center gap-3 border-t border-border pt-5">
        <Button
          onClick={() => start.mutate()}
          disabled={!canStart || start.isPending}
          className="h-10 min-w-28"
        >
          {start.isPending ? 'Загружаем…' : 'Начать'}
        </Button>
        <p className="text-2xs text-muted-foreground">
          {mode === 'category' && categoryIds.length === 0
            ? 'Выберите хотя бы одну тему'
            : mode === 'tournament' && packId === null
              ? 'Выберите турнир'
              : mode === 'weak' && weakIds.length === 0
                ? 'Слабые темы не измерены'
                : mode === 'tournament' && selectedTournament?.questions_count === count
                  ? `Весь турнир · ${count} ${questionsWord(count)} · результаты сохранятся`
                  : `${count} ${questionsWord(count)} · результаты сохранятся`}
        </p>
      </div>

      {meta && mode === 'category' && (
        <p className="mt-4 text-2xs leading-relaxed text-muted-foreground">
          По темам доступно {num(meta.classified)} {questionsWord(meta.classified)} —{' '}
          {meta.classification_pct}% базы. Режим «Случайные» берёт вопросы из всей базы.
        </p>
      )}
      {meta && mode === 'marked' && (
        <p className="mt-4 text-2xs leading-relaxed text-muted-foreground">
          В выборке участвуют {num(meta.classified)} {questionsWord(meta.classified)} с
          определённой темой — {meta.classification_pct}% всей базы.
        </p>
      )}
    </Page>
  )
}
