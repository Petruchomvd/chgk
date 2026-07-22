import { useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { Search, X, ChevronLeft, ChevronRight } from 'lucide-react'
import { api, type CatalogFilters } from '@/lib/api'
import { Page, PageHeader } from '@/components/AppShell'
import { Empty, ErrorState, RowSkeleton, loadError } from '@/components/States'
import { QuestionRow } from '@/components/QuestionBits'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { num, questionsWord } from '@/lib/format'
import { cn } from '@/lib/utils'

const PAGE_SIZE = 30
const ALL = '__all__'

const STATUS_OPTIONS = [
  { value: ALL, label: 'Любой статус' },
  { value: 'new', label: 'Не встречался' },
  { value: 'seen', label: 'Уже был' },
  { value: 'failed', label: 'Ошибался' },
  { value: 'known', label: 'Знал' },
  { value: 'due', label: 'К повторению' },
]

// Слои беручести. Те же пороги, что в тренировке и в scripts/team_gap.py.
// difficulty = 10 × (1 − доля взявших), поэтому границы перевёрнуты.
const LAYER_OPTIONS = [
  { value: ALL, label: 'Любая беручесть', range: null },
  { value: 'easy', label: 'Взяли ≥85%', range: [0, 1.5] },
  { value: 'medium-easy', label: 'Взяли 70–85%', range: [1.5, 3.0] },
  { value: 'medium', label: 'Взяли 40–70%', range: [3.0, 6.0] },
  { value: 'hard', label: 'Взяли 15–40%', range: [6.0, 8.5] },
  { value: 'brutal', label: 'Взяли <15%', range: [8.5, 10] },
] as const

const SORT_OPTIONS = [
  { value: 'recent', label: 'Сначала свежие' },
  { value: 'oldest', label: 'Сначала старые' },
  { value: 'difficulty_desc', label: 'Сложные первыми' },
  { value: 'difficulty_asc', label: 'Простые первыми' },
]

function useDebounced<T>(value: T, delay = 250): T {
  const [v, setV] = useState(value)
  useEffect(() => {
    const t = setTimeout(() => setV(value), delay)
    return () => clearTimeout(t)
  }, [value, delay])
  return v
}

export function Catalog() {
  const [params, setParams] = useSearchParams()
  const searchRef = useRef<HTMLInputElement>(null)

  const [searchInput, setSearchInput] = useState(params.get('q') ?? '')
  const search = useDebounced(searchInput)

  const categoryName = params.get('category')
  const status = params.get('status')
  const yearFrom = params.get('year_from')
  const yearTo = params.get('year_to')
  const layer = params.get('layer')
  const sort = params.get('sort') ?? 'recent'
  const page = Number(params.get('page') ?? 1)

  const { data: meta } = useQuery({ queryKey: ['meta'], queryFn: api.meta, staleTime: Infinity })
  const categoryId = meta?.categories.find((c) => c.name_ru === categoryName)?.id ?? null

  // Смена фильтра всегда возвращает на первую страницу.
  const patch = (next: Record<string, string | null>) => {
    const p = new URLSearchParams(params)
    for (const [k, v] of Object.entries(next)) {
      if (v === null || v === '') p.delete(k)
      else p.set(k, v)
    }
    if (!('page' in next)) p.delete('page')
    setParams(p, { replace: true })
  }

  useEffect(() => {
    patch({ q: search || null })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search])

  const layerRange = LAYER_OPTIONS.find((l) => l.value === layer)?.range ?? null

  const filters: CatalogFilters = {
    search,
    category_id: categoryId,
    status: status,
    year_from: yearFrom ? Number(yearFrom) : null,
    year_to: yearTo ? Number(yearTo) : null,
    difficulty_min: layerRange ? layerRange[0] : null,
    difficulty_max: layerRange ? layerRange[1] : null,
    sort,
    limit: PAGE_SIZE,
    offset: (page - 1) * PAGE_SIZE,
  }

  const { data, isPending, isFetching, error, fetchStatus, refetch } = useQuery({
    queryKey: ['questions', filters],
    queryFn: () => api.questions(filters),
    // Список не мигает при пагинации — старые данные держатся до новых.
    placeholderData: keepPreviousData,
  })

  const err = loadError(error, fetchStatus)

  // «/» ставит фокус в поиск — как в читалках и почтовых клиентах.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null
      const typing = target && /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName)
      if (e.key === '/' && !typing) {
        e.preventDefault()
        searchRef.current?.focus()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const total = data?.total ?? 0
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const activeFilters = [categoryName, status, yearFrom, yearTo].filter(Boolean).length

  return (
    <Page wide>
      <PageHeader
        title="Картотека"
        meta={
          data ? `${num(total)} ${questionsWord(total)}` : meta ? `${num(meta.total_questions)}` : undefined
        }
      />

      {/* ─── Поиск и фильтры: всегда на виду, не в меню ─────────── */}
      <div className="space-y-2.5">
        <div className="relative">
          <Search
            className="pointer-events-none absolute top-1/2 left-3 size-3.5 -translate-y-1/2 text-muted-foreground"
            aria-hidden
          />
          <Input
            ref={searchRef}
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            placeholder="Поиск по тексту вопроса и ответу…"
            aria-label="Поиск по тексту вопроса и ответу"
            className="h-9 pr-16 pl-9"
          />
          {searchInput ? (
            <button
              type="button"
              onClick={() => setSearchInput('')}
              aria-label="Очистить поиск"
              className="absolute top-1/2 right-2.5 -translate-y-1/2 rounded-sm p-0.5
                         text-muted-foreground hover:text-foreground"
            >
              <X className="size-3.5" aria-hidden />
            </button>
          ) : (
            <span className="kbd absolute top-1/2 right-2.5 -translate-y-1/2">/</span>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Select
            value={categoryName ?? ALL}
            onValueChange={(v) => patch({ category: v === ALL ? null : v })}
          >
            <SelectTrigger size="sm" className="w-[168px]" aria-label="Тема">
              {/* Base UI отдаёт в Value сырое значение — подставляем подпись сами. */}
              <SelectValue>{(v) => (v === ALL || !v ? 'Все темы' : String(v))}</SelectValue>
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>Все темы</SelectItem>
              {meta?.categories.map((c) => (
                <SelectItem key={c.id} value={c.name_ru}>
                  {c.name_ru}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          <Select
            value={status ?? ALL}
            onValueChange={(v) => patch({ status: v === ALL ? null : v })}
          >
            <SelectTrigger size="sm" className="w-[150px]" aria-label="Статус изучения">
              <SelectValue>
                {(v) =>
                  STATUS_OPTIONS.find((o) => o.value === (v ?? ALL))?.label ?? 'Любой статус'
                }
              </SelectValue>
            </SelectTrigger>
            <SelectContent>
              {STATUS_OPTIONS.map((o) => (
                <SelectItem key={o.value} value={o.value}>
                  {o.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          <Select
            value={layer ?? ALL}
            onValueChange={(v) => patch({ layer: v === ALL ? null : v })}
          >
            <SelectTrigger size="sm" className="w-[164px]" aria-label="Беручесть вопроса">
              <SelectValue>
                {(v) =>
                  LAYER_OPTIONS.find((o) => o.value === (v ?? ALL))?.label ??
                  'Любая беручесть'
                }
              </SelectValue>
            </SelectTrigger>
            <SelectContent>
              {LAYER_OPTIONS.map((o) => (
                <SelectItem key={o.value} value={o.value}>
                  {o.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          <div className="flex items-center gap-1.5">
            <Input
              type="number"
              inputMode="numeric"
              placeholder="с 2000"
              aria-label="Год с"
              value={yearFrom ?? ''}
              onChange={(e) => patch({ year_from: e.target.value || null })}
              className="tabular h-8 w-[86px]"
            />
            <span className="text-2xs text-muted-foreground">—</span>
            <Input
              type="number"
              inputMode="numeric"
              placeholder="по 2026"
              aria-label="Год по"
              value={yearTo ?? ''}
              onChange={(e) => patch({ year_to: e.target.value || null })}
              className="tabular h-8 w-[86px]"
            />
          </div>

          <Select value={sort} onValueChange={(v) => patch({ sort: v })}>
            <SelectTrigger size="sm" className="w-[152px]" aria-label="Сортировка">
              <SelectValue>
                {(v) => SORT_OPTIONS.find((o) => o.value === v)?.label ?? 'Сначала свежие'}
              </SelectValue>
            </SelectTrigger>
            <SelectContent>
              {SORT_OPTIONS.map((o) => (
                <SelectItem key={o.value} value={o.value}>
                  {o.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          {activeFilters > 0 && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setParams(new URLSearchParams(), { replace: true })}
            >
              <X className="size-3" aria-hidden />
              Сбросить
            </Button>
          )}

          {/* Индикатор фонового запроса — не двигает раскладку. */}
          <span
            className={cn(
              'ml-auto text-2xs text-muted-foreground transition-opacity',
              isFetching && !isPending ? 'opacity-100' : 'opacity-0',
            )}
            aria-live="polite"
          >
            загрузка…
          </span>
        </div>
      </div>

      {/* ─── Список ────────────────────────────────────────────── */}
      <div className="mt-4 overflow-hidden rounded-lg border border-border bg-paper-raised">
        {err ? (
          <div className="p-4">
            <ErrorState error={err} onRetry={() => refetch()} />
          </div>
        ) : isPending ? (
          <RowSkeleton />
        ) : data && data.items.length === 0 ? (
          <Empty
            className="border-0 bg-transparent"
            title="Ничего не найдено"
            hint={
              search
                ? `По запросу «${search}» с текущими фильтрами вопросов нет. Попробуйте другое слово или снимите фильтры.`
                : 'С текущими фильтрами вопросов нет.'
            }
            action={
              activeFilters > 0 || search ? (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    setSearchInput('')
                    setParams(new URLSearchParams(), { replace: true })
                  }}
                >
                  Сбросить всё
                </Button>
              ) : undefined
            }
          />
        ) : (
          <ul className="divide-y divide-border">
            {data?.items.map((item) => (
              <li key={item.id}>
                <QuestionRow item={item} />
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* ─── Пагинация ─────────────────────────────────────────── */}
      {total > PAGE_SIZE && (
        <div className="mt-4 flex items-center justify-between gap-3">
          <Button
            variant="outline"
            size="sm"
            disabled={page <= 1}
            onClick={() => patch({ page: String(page - 1) })}
          >
            <ChevronLeft className="size-3.5" aria-hidden />
            Назад
          </Button>
          <span className="tabular text-2xs text-muted-foreground">
            {num((page - 1) * PAGE_SIZE + 1)}–{num(Math.min(page * PAGE_SIZE, total))} из{' '}
            {num(total)}
          </span>
          <Button
            variant="outline"
            size="sm"
            disabled={page >= totalPages}
            onClick={() => patch({ page: String(page + 1) })}
          >
            Вперёд
            <ChevronRight className="size-3.5" aria-hidden />
          </Button>
        </div>
      )}
    </Page>
  )
}
