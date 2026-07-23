import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api, type MapPoint } from '@/lib/api'
import { Page, PageHeader } from '@/components/AppShell'
import { ErrorState, BlockSkeleton, loadError } from '@/components/States'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

/** Цвета категорий — фиксированные, подобраны под светлый фон приложения. */
const PALETTE: Record<string, string> = {
  'Быт и повседневность': '#7A6F42',
  'География': '#0E8A7B',
  'Искусство': '#7A50C7',
  'История': '#C05F3C',
  'Кино и театр': '#C23A4C',
  'Литература': '#3F8F2E',
  'Логика и wordplay': '#B08F00',
  'Музыка': '#B06F10',
  'Наука и технологии': '#1D7FA8',
  'Общество и политика': '#3A62C4',
  'Природа и животные': '#1D7A4C',
  'Религия и мифология': '#6355D6',
  'Спорт': '#C24A92',
  'Язык и лингвистика': '#A03BC0',
}

/** Приёмы: вторая ось разметки. Цвета не пересекаются с категориальными. */
const TECH_PALETTE: Record<string, string> = {
  'чистый': '#A8A093',
  'замена': '#B08F00',
  'пропуск': '#3F8F2E',
  'раздатка': '#1D7FA8',
  'блиц': '#C23A4C',
  'цитата': '#7A50C7',
}

type View = { k: number; tx: number; ty: number }
type ColorMode = 'cat' | 'tech'

export function SemanticMap() {
  const navigate = useNavigate()
  const qc = useQueryClient()
  /** Полные тексты, догруженные при наведении. В выгрузке карты — только
   *  обрезки: полные тексты всех 30k точек весили бы ~18 МБ. */
  const fullRef = useRef<Map<number, { text: string; answer: string }>>(new Map())
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const [view, setView] = useState<View>({ k: 1, tx: 0, ty: 0 })
  const [off, setOff] = useState<Set<string>>(new Set())
  const [mode, setMode] = useState<ColorMode>('cat')
  const [hover, setHover] = useState<{ p: MapPoint; sx: number; sy: number } | null>(null)
  const dragRef = useRef<{ x: number; y: number; view: View } | null>(null)
  const movedRef = useRef(false)

  const { data, isPending, error, fetchStatus, refetch } = useQuery({
    queryKey: ['semantic-map'],
    queryFn: () => api.semanticMap(),
    staleTime: Infinity,
  })

  const keyOf = (p: MapPoint) => (mode === 'cat' ? p.c : p.h)
  const colorOf = (p: MapPoint) =>
    (mode === 'cat' ? PALETTE[p.c] : TECH_PALETTE[p.h]) ?? '#888'
  const legendColor = (name: string) =>
    (mode === 'cat' ? PALETTE[name] : TECH_PALETTE[name]) ?? '#888'

  const cats = useMemo(
    () => (data ? [...new Set(data.points.map((p) => (mode === 'cat' ? p.c : p.h)))].sort((a, b) => a.localeCompare(b, 'ru')) : []),
    [data, mode],
  )
  const counts = useMemo(() => {
    const m: Record<string, number> = {}
    data?.points.forEach((p) => { const k = mode === 'cat' ? p.c : p.h; m[k] = (m[k] ?? 0) + 1 })
    return m
  }, [data, mode])

  // Границы проекции — чтобы вписать облако в холст при view = identity.
  const bounds = useMemo(() => {
    if (!data?.points.length) return null
    const xs = data.points.map((p) => p.x)
    const ys = data.points.map((p) => p.y)
    return { x0: Math.min(...xs), x1: Math.max(...xs), y0: Math.min(...ys), y1: Math.max(...ys) }
  }, [data])

  const project = useMemo(() => {
    return (p: MapPoint, w: number, h: number) => {
      if (!bounds) return { x: 0, y: 0 }
      const pad = 24
      const bx = pad + ((p.x - bounds.x0) / (bounds.x1 - bounds.x0)) * (w - 2 * pad)
      const by = pad + ((p.y - bounds.y0) / (bounds.y1 - bounds.y0)) * (h - 2 * pad)
      return { x: bx * view.k + view.tx, y: by * view.k + view.ty }
    }
  }, [bounds, view])

  // Отрисовка
  useEffect(() => {
    const cv = canvasRef.current
    if (!cv || !data) return
    const dpr = window.devicePixelRatio || 1
    const rect = cv.getBoundingClientRect()
    cv.width = rect.width * dpr
    cv.height = rect.height * dpr
    const g = cv.getContext('2d')!
    g.setTransform(dpr, 0, 0, dpr, 0, 0)
    g.clearRect(0, 0, rect.width, rect.height)
    const isolating = off.size > 0
    const r = Math.max(1.6, Math.min(3.2, 1.6 * view.k))
    for (const p of data.points) {
      const hidden = off.has(keyOf(p))
      const { x, y } = project(p, rect.width, rect.height)
      if (x < -5 || y < -5 || x > rect.width + 5 || y > rect.height + 5) continue
      if (hidden) { g.globalAlpha = 0.07; g.fillStyle = '#9a9483' }
      else { g.globalAlpha = isolating ? 0.95 : 0.75; g.fillStyle = colorOf(p) }
      const rr = hidden ? 1.4 : r
      g.fillRect(x - rr, y - rr, rr * 2, rr * 2)
    }
    g.globalAlpha = 1
  }, [data, view, off, project, mode])

  const findNearest = (clientX: number, clientY: number): { p: MapPoint; sx: number; sy: number } | null => {
    const cv = canvasRef.current
    if (!cv || !data) return null
    const rect = cv.getBoundingClientRect()
    const mx = clientX - rect.left
    const my = clientY - rect.top
    let best: MapPoint | null = null
    let bx = 0, by = 0, bd = 144 // радиус захвата 12px
    for (const p of data.points) {
      if (off.has(keyOf(p))) continue
      const { x, y } = project(p, rect.width, rect.height)
      const d = (x - mx) ** 2 + (y - my) ** 2
      if (d < bd) { bd = d; best = p; bx = x; by = y }
    }
    return best ? { p: best, sx: bx, sy: by } : null
  }

  // Ленивая догрузка полного текста под курсором. Дебаунс отсекает пролёты;
  // кэш общий со страницей вопроса — наведение прогревает будущий клик.
  useEffect(() => {
    const id = hover?.p.id
    if (!id || fullRef.current.has(id)) return
    const t = setTimeout(() => {
      qc.fetchQuery({ queryKey: ['question', id], queryFn: () => api.question(id), staleTime: Infinity })
        .then((q) => {
          fullRef.current.set(id, { text: q.text, answer: q.answer })
          setHover((h) => (h && h.p.id === id ? { ...h } : h))
        })
        .catch(() => { /* сеть мигнула — остаёмся на обрезке */ })
    }, 120)
    return () => clearTimeout(t)
  }, [hover?.p.id, qc])

  // Колесо вешаем НАТИВНО и non-passive: React-обработчики wheel пассивные,
  // preventDefault в них игнорируется — и зум карты зумил всю страницу.
  useEffect(() => {
    const cv = canvasRef.current
    if (!cv) return
    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      const rect = cv.getBoundingClientRect()
      const mx = e.clientX - rect.left
      const my = e.clientY - rect.top
      const factor = e.deltaY < 0 ? 1.18 : 1 / 1.18
      setView((v) => {
        const k = Math.min(24, Math.max(0.6, v.k * factor))
        const real = k / v.k
        return { k, tx: mx - (mx - v.tx) * real, ty: my - (my - v.ty) * real }
      })
    }
    cv.addEventListener('wheel', onWheel, { passive: false })
    return () => cv.removeEventListener('wheel', onWheel)
  }, [data])

  return (
    <Page>
      <PageHeader title="Смысловая карта" />
      <p className="mb-4 max-w-2xl text-xs text-muted-foreground">
        30 000 размеченных вопросов, спроецированных из 384-мерного пространства эмбеддингов
        на плоскость. Колесо — зум, перетаскивание — панорама, клик по точке открывает вопрос
        в картотеке. Клик по категории справа — изолировать её облако.
      </p>

      {loadError(error, fetchStatus) ? (
        <ErrorState error={loadError(error, fetchStatus)!} onRetry={() => refetch()} />
      ) : isPending || !data ? (
        <>
          <p className="mb-2 text-2xs text-muted-foreground">
            Загружаем готовую проекцию…
          </p>
          <BlockSkeleton className="h-[560px]" />
        </>
      ) : !data.available ? (
        <p className="text-xs text-muted-foreground">
          Карта ещё не выгружена с Mac. Подготовьте её командой{' '}
          <code>python scripts/export_semantic_map.py</code>.
        </p>
      ) : (
        <div className="grid gap-3 md:grid-cols-[1fr_220px]">
          <div className="relative">
            <canvas
              ref={canvasRef}
              className="h-[560px] w-full cursor-crosshair touch-none select-none rounded-lg border border-border bg-card"
              style={{ overscrollBehavior: "contain" }}
              onMouseDown={(e) => {
                dragRef.current = { x: e.clientX, y: e.clientY, view }
                movedRef.current = false
              }}
              onMouseMove={(e) => {
                const d = dragRef.current
                if (d) {
                  const dx = e.clientX - d.x
                  const dy = e.clientY - d.y
                  if (Math.abs(dx) + Math.abs(dy) > 3) movedRef.current = true
                  setView({ k: d.view.k, tx: d.view.tx + dx, ty: d.view.ty + dy })
                  setHover(null)
                } else {
                  setHover(findNearest(e.clientX, e.clientY))
                }
              }}
              onMouseUp={(e) => {
                dragRef.current = null
                if (!movedRef.current) {
                  const hit = findNearest(e.clientX, e.clientY)
                  if (hit) navigate(`/question/${hit.p.id}`)
                }
              }}
              onMouseLeave={() => { dragRef.current = null; setHover(null) }}
            />
            {hover && (
              <div
                className="pointer-events-none absolute z-10 max-w-sm rounded-md border border-border bg-card px-3 py-2 text-2xs shadow-md"
                style={{ left: Math.min(hover.sx + 14, 640), top: hover.sy + 14 }}
              >
                <span className="font-medium" style={{ color: colorOf(hover.p) }}>
                  {hover.p.c}
                  <span className="ml-2 text-muted-foreground">· {hover.p.h}</span>
                </span>
                {(() => {
                  const full = fullRef.current.get(hover.p.id)
                  return (
                    <>
                      <p className="mt-0.5 max-h-64 overflow-hidden whitespace-pre-wrap text-foreground">
                        {full ? full.text : `${hover.p.t}…`}
                      </p>
                      <p className="mt-1 text-amber-ink">
                        Ответ: {full ? full.answer : hover.p.a}
                      </p>
                    </>
                  )
                })()}
              </div>
            )}
            <Button
              variant="ghost"
              size="sm"
              className="absolute right-2 top-2 h-7 text-2xs"
              onClick={() => setView({ k: 1, tx: 0, ty: 0 })}
            >
              Сбросить вид
            </Button>
          </div>

          <div className="flex flex-col gap-0.5 self-start">
            <div className="mb-2 flex gap-1">
              {([['cat', 'Категории'], ['tech', 'Приёмы']] as const).map(([m, label]) => (
                <button
                  key={m}
                  className={cn(
                    'rounded-md border px-2.5 py-1 text-2xs',
                    mode === m
                      ? 'border-amber bg-amber-wash text-amber-ink'
                      : 'border-border text-muted-foreground hover:text-foreground',
                  )}
                  onClick={() => { setMode(m); setOff(new Set()) }}
                >
                  {label}
                </button>
              ))}
            </div>
            {cats.map((c) => (
              <button
                key={c}
                className={cn(
                  'flex items-center gap-2 rounded-md border border-transparent px-2 py-1 text-left text-2xs hover:border-border',
                  off.has(c) && 'opacity-30',
                )}
                onClick={() =>
                  setOff((prev) => {
                    // клик по единственной активной — вернуть все
                    if (prev.size === 0) return new Set(cats.filter((x) => x !== c))
                    const next = new Set(prev)
                    if (next.has(c)) next.delete(c)
                    else next.add(c)
                    return next.size === cats.length ? new Set() : next
                  })
                }
              >
                <span className="size-2.5 shrink-0 rounded-full" style={{ background: legendColor(c) }} />
                <span className="truncate">{c}</span>
                <span className="tabular ml-auto text-muted-foreground">{counts[c]}</span>
              </button>
            ))}
            <p className="mt-2 text-2xs leading-relaxed text-muted-foreground">
              Жёлтая «Логика» растворена по всей карте: каламбур живёт там, где его материал, —
              у пространства смыслов нет полки для приёмов.
            </p>
          </div>
        </div>
      )}
    </Page>
  )
}
