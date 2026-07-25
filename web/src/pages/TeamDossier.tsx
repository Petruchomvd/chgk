import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Target, TrendingDown, Trophy, Crosshair, Users, UserPlus } from 'lucide-react'
import { api, type CalibrationBand } from '@/lib/api'
import { Page } from '@/components/AppShell'
import { ErrorState, BlockSkeleton, loadError } from '@/components/States'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'

const pct = (x: number) => `${Math.round(x * 100)}%`
const signed = (x: number, digits = 1) => `${x >= 0 ? '+' : ''}${x.toFixed(digits)}`

function relativeActivity(value: string | null) {
  if (!value) return 'ещё не тренировался'
  const days = Math.floor((Date.now() - new Date(value).getTime()) / 86_400_000)
  if (days <= 0) return 'сегодня'
  if (days === 1) return 'вчера'
  if (days < 7) return `${days} дн. назад`
  return new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'short' }).format(
    new Date(value),
  )
}

function deltaClass(x: number) {
  return x >= 0 ? 'text-emerald-600' : 'text-rose-600'
}

/** Калибровочная кривая: где на шкале сложности команда теряет очки.
 *  Две тонкие полосы на диапазон — поле и команда; чип отклонения. */
function Calibration({ bands, focus }: { bands: CalibrationBand[]; focus?: [number, number] }) {
  return (
    <div className="space-y-2.5">
      {bands.map((b) => {
        const inFocus =
          focus && b.band[0] >= focus[0] - 1e-6 && b.band[1] <= focus[1] + 1e-6
        return (
          <div
            key={b.band[0]}
            className={`rounded-md px-2.5 py-2 ${
              inFocus ? 'bg-amber-wash/50 ring-1 ring-amber-ink/20' : ''
            }`}
          >
            <div className="mb-1 flex items-center justify-between text-2xs">
              <span className="font-medium">
                {pct(b.band[0])}–{pct(b.band[1])} беручести
                <span className="ml-1.5 text-muted-foreground">· {b.questions} в.</span>
              </span>
              <span className={`tabular font-semibold ${deltaClass(b.lift)}`}>
                {signed(b.lift * 100)} п.п.
              </span>
            </div>
            <Bar label="поле" value={b.field_rate} tone="muted" />
            <Bar label="мы" value={b.team_rate} tone="amber" />
          </div>
        )
      })}
    </div>
  )
}

function Bar({ label, value, tone }: { label: string; value: number; tone: 'muted' | 'amber' }) {
  return (
    <div className="flex items-center gap-2">
      <span className="w-8 shrink-0 text-2xs text-muted-foreground">{label}</span>
      <div className="h-2.5 flex-1 overflow-hidden rounded-sm bg-ink-line/40">
        <div
          className={`h-full rounded-sm ${tone === 'amber' ? 'bg-amber-ink' : 'bg-muted-foreground/45'}`}
          style={{ width: `${Math.max(1, value * 100)}%` }}
        />
      </div>
      <span className="tabular w-9 shrink-0 text-right text-2xs text-muted-foreground">
        {pct(value)}
      </span>
    </div>
  )
}

function Stat({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: string }) {
  return (
    <div className="rounded-lg border border-border px-3 py-2.5">
      <div className="text-2xs uppercase tracking-wide text-muted-foreground">{label}</div>
      <div className={`text-xl font-semibold tabular ${tone ?? ''}`}>{value}</div>
      {sub && <div className="text-2xs text-muted-foreground">{sub}</div>}
    </div>
  )
}

/** Прогноз счёта на пакет: поиск турнира → ожидаемый счёт + swing-вопросы. */
function Forecast() {
  const [draft, setDraft] = useState('')
  const [search, setSearch] = useState('')

  const { data: found } = useQuery({
    queryKey: ['tournaments', search],
    queryFn: () => api.tournaments(search),
    enabled: search.trim().length >= 3,
  })

  const forecast = useMutation({
    mutationFn: (pack: number) => api.teamForecast(pack),
  })

  const submit = (e: React.FormEvent) => {
    e.preventDefault()
    setSearch(draft.trim())
  }

  const f = forecast.data
  return (
    <div>
      <form onSubmit={submit} className="mb-3 flex gap-2">
        <Input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="название турнира, например: Крафтик"
          className="h-9"
        />
        <Button type="submit" className="h-9 shrink-0" disabled={draft.trim().length < 3}>
          Найти пакет
        </Button>
      </form>

      {found && found.items.length > 0 && !f && (
        <ul className="mb-4 divide-y divide-border border-y border-border">
          {found.items.slice(0, 8).map((t) => (
            <li key={t.id} className="flex items-center justify-between gap-3 py-2">
              <span className="truncate text-xs">{t.title}</span>
              <Button
                variant="outline"
                className="h-7 shrink-0 text-2xs"
                onClick={() => forecast.mutate(t.id)}
              >
                Прогноз ({t.questions_count} в.)
              </Button>
            </li>
          ))}
        </ul>
      )}

      {forecast.isPending && <BlockSkeleton className="h-24" />}
      {forecast.error && (
        <p className="text-xs text-rose-600">
          {(forecast.error as Error).message}
        </p>
      )}

      {f && (
        <div className="rounded-lg border border-border p-3">
          <div className="mb-2 flex items-center justify-between gap-2">
            <span className="truncate text-sm font-medium">{f.pack_title}</span>
            <button
              className="shrink-0 text-2xs text-muted-foreground underline"
              onClick={() => forecast.reset()}
            >
              другой пакет
            </button>
          </div>
          <div className="mb-3 grid grid-cols-3 gap-2">
            <Stat label="наш прогноз" value={`${f.expected}`} sub={`из ${f.questions}`} tone="text-amber-ink" />
            <Stat label="средняя поля" value={`${f.field_avg}`} sub={`перевес ${signed(f.expected - f.field_avg)}`} />
            <Stat label="банкеры" value={`${f.bankers}`} sub="P≥85%, терять нельзя" />
          </div>
          <p className="mb-2 text-2xs text-muted-foreground">
            Прогноз — средняя форма команды; разброс между турнирами ±7 вопросов
            (одна хорошая игра может дать сильно больше).
          </p>
          {f.swing.length > 0 && (
            <>
              <div className="mb-1.5 flex items-center gap-1.5 text-2xs font-medium">
                <Crosshair className="size-3" aria-hidden />
                Swing-вопросы — решают исход (P≈50% в зоне фокуса)
              </div>
              <ul className="divide-y divide-border border-t border-border">
                {f.swing.map((s) => (
                  <li key={s.qid} className="py-2">
                    <Link to={`/question/${s.qid}`} className="group block">
                      <div className="mb-0.5 flex items-center gap-2 text-2xs text-muted-foreground">
                        <span className="tabular rounded bg-amber-wash/60 px-1.5 py-0.5 text-amber-ink">
                          P {pct(s.p)}
                        </span>
                        <span className="tabular">поле {pct(s.tr)}</span>
                        {s.category && <span className="text-amber-ink">{s.category}</span>}
                      </div>
                      <p className="text-xs leading-relaxed group-hover:text-amber-ink">
                        {s.text}…
                      </p>
                    </Link>
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      )}
    </div>
  )
}

export function TeamDossier() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [showCreatePlayer, setShowCreatePlayer] = useState(false)
  const [playerForm, setPlayerForm] = useState({
    display_name: '',
    username: '',
    password: '',
    telegram_id: '',
    vk_id: '',
  })
  const { data, isPending, error, fetchStatus, refetch } = useQuery({
    queryKey: ['team-dossier'],
    queryFn: api.teamDossier,
  })

  const startGap = useMutation({
    mutationFn: () => api.startTraining({ mode: 'team_gap', count: 12 }),
    onSuccess: (s) => navigate(`/training/${s.session_id}`),
  })

  const createPlayer = useMutation({
    mutationFn: () =>
      api.createPlayer({
        display_name: playerForm.display_name.trim(),
        username: playerForm.username.trim(),
        password: playerForm.password,
        telegram_id: playerForm.telegram_id.trim() || null,
        vk_id: playerForm.vk_id.trim() || null,
      }),
    onSuccess: () => {
      setPlayerForm({
        display_name: '',
        username: '',
        password: '',
        telegram_id: '',
        vk_id: '',
      })
      setShowCreatePlayer(false)
      queryClient.invalidateQueries({ queryKey: ['team-dossier'] })
    },
  })

  const err = loadError(error, fetchStatus)
  if (err) return <Page><ErrorState error={err} onRetry={() => refetch()} /></Page>
  if (isPending || !data) return <Page><BlockSkeleton className="h-40" /></Page>
  if (!data.available)
    return (
      <Page>
        <p className="text-xs text-muted-foreground">
          Профиль команды не построен: <code>python scripts/team_history.py --team-id 97700</code>
        </p>
      </Page>
    )

  const deficit = (data.took ?? 0) - (data.expected ?? 0)
  const reliableCats = (data.categories ?? []).filter((c) => c.questions >= 5).slice(0, 6)

  return (
    <Page>
      <h1 className="mb-1 text-lg font-semibold">Досье команды · {data.team_title}</h1>
      <p className="mb-4 text-xs text-muted-foreground">
        Измерено на {data.tournaments?.length} турнирах ({data.questions_total} вопросов):
        сравнение с полем на тех же вопросах, а не самооценка. Данные —{' '}
        <code>scripts/team_history.py</code>.
      </p>

      <section className="mb-7">
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <div className="flex items-center gap-1.5">
            <Users className="size-4 text-amber-ink" aria-hidden />
            <h2 className="text-sm font-semibold">Тренировки игроков</h2>
          </div>
          <Button
            variant="outline"
            className="ml-auto h-8 text-2xs"
            onClick={() => {
              createPlayer.reset()
              setShowCreatePlayer((shown) => !shown)
            }}
          >
            <UserPlus className="size-3.5" aria-hidden />
            Новый аккаунт
          </Button>
        </div>
        <p className="mb-3 text-2xs text-muted-foreground">
          Активность на сайте и в подключённых ботах хранится в общей истории игрока.
        </p>
        {showCreatePlayer && (
          <form
            className="mb-4 rounded-lg border border-border bg-paper-raised p-3.5"
            onSubmit={(event) => {
              event.preventDefault()
              createPlayer.mutate()
            }}
          >
            <div className="mb-3">
              <h3 className="text-xs font-semibold">Создать аккаунт игрока</h3>
              <p className="mt-0.5 text-2xs text-muted-foreground">
                Имя и логин можно сообщить игроку. Пароль хранится в защищённом виде.
              </p>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="space-y-1 text-2xs font-medium">
                <span>Имя игрока</span>
                <Input
                  value={playerForm.display_name}
                  onChange={(event) =>
                    setPlayerForm((form) => ({ ...form, display_name: event.target.value }))
                  }
                  placeholder="Например, Анна"
                  required
                />
              </label>
              <label className="space-y-1 text-2xs font-medium">
                <span>Логин</span>
                <Input
                  value={playerForm.username}
                  onChange={(event) =>
                    setPlayerForm((form) => ({ ...form, username: event.target.value }))
                  }
                  placeholder="anna"
                  autoCapitalize="none"
                  required
                />
              </label>
              <label className="space-y-1 text-2xs font-medium">
                <span>Временный пароль</span>
                <Input
                  type="password"
                  value={playerForm.password}
                  onChange={(event) =>
                    setPlayerForm((form) => ({ ...form, password: event.target.value }))
                  }
                  placeholder="Не короче 10 символов"
                  minLength={10}
                  required
                />
              </label>
              <label className="space-y-1 text-2xs font-medium">
                <span>
                  Telegram{' '}
                  <span className="font-normal text-muted-foreground">
                    ID или @username, необязательно
                  </span>
                </span>
                <Input
                  type="text"
                  inputMode="text"
                  autoComplete="off"
                  autoCapitalize="none"
                  autoCorrect="off"
                  spellCheck={false}
                  value={playerForm.telegram_id}
                  onChange={(event) =>
                    setPlayerForm((form) => ({
                      ...form,
                      telegram_id: event.target.value,
                    }))
                  }
                  placeholder="@mtv3dd или 123456789"
                />
              </label>
              <label className="space-y-1 text-2xs font-medium">
                <span>VK ID <span className="font-normal text-muted-foreground">необязательно</span></span>
                <Input
                  type="text"
                  inputMode="numeric"
                  pattern="[0-9]*"
                  autoComplete="off"
                  autoCorrect="off"
                  spellCheck={false}
                  value={playerForm.vk_id}
                  onChange={(event) =>
                    setPlayerForm((form) => ({
                      ...form,
                      vk_id: event.target.value,
                    }))
                  }
                  placeholder="Числовой ID для истории с VK-ботом"
                />
              </label>
            </div>
            {createPlayer.error && (
              <p className="mt-3 text-2xs text-rose-600">
                {(createPlayer.error as Error).message}
              </p>
            )}
            <div className="mt-3 flex gap-2">
              <Button type="submit" className="h-8 text-2xs" disabled={createPlayer.isPending}>
                {createPlayer.isPending ? 'Создаём…' : 'Создать аккаунт'}
              </Button>
              <Button
                type="button"
                variant="ghost"
                className="h-8 text-2xs"
                onClick={() => setShowCreatePlayer(false)}
              >
                Отмена
              </Button>
            </div>
          </form>
        )}
        <div className="overflow-hidden rounded-lg border border-border bg-paper-raised">
          <div className="hidden grid-cols-[minmax(140px,1fr)_100px_90px_90px_100px] gap-3 border-b border-border px-3.5 py-2 text-2xs font-medium tracking-wide text-muted-foreground uppercase sm:grid">
            <span>Игрок</span>
            <span className="text-right">За 7 дней</span>
            <span className="text-right">Всего</span>
            <span className="text-right">Успех</span>
            <span className="text-right">Последняя</span>
          </div>
          <ul className="divide-y divide-border">
            {(data.players ?? []).map((player) => (
              <li
                key={player.id}
                className="grid gap-2 px-3.5 py-3 text-xs sm:grid-cols-[minmax(140px,1fr)_100px_90px_90px_100px] sm:items-center sm:gap-3"
              >
                <div className="min-w-0">
                  <div className="truncate font-medium">{player.display_name}</div>
                  <div className="truncate text-2xs text-muted-foreground">
                    @{player.username}{player.role === 'owner' ? ' · владелец' : ''}
                  </div>
                </div>
                <div className="flex justify-between sm:block sm:text-right">
                  <span className="text-2xs text-muted-foreground sm:hidden">За 7 дней</span>
                  <span className="tabular">{player.attempts_7d}</span>
                </div>
                <div className="flex justify-between sm:block sm:text-right">
                  <span className="text-2xs text-muted-foreground sm:hidden">Вопросов</span>
                  <span className="tabular">{player.questions}</span>
                </div>
                <div className="flex justify-between sm:block sm:text-right">
                  <span className="text-2xs text-muted-foreground sm:hidden">Успех</span>
                  <span className="tabular">
                    {player.success_pct === null ? '—' : `${Math.round(player.success_pct)}%`}
                  </span>
                </div>
                <div className="flex justify-between text-2xs text-muted-foreground sm:block sm:text-right">
                  <span className="sm:hidden">Последняя</span>
                  <span>{relativeActivity(player.last_attempt_at)}</span>
                </div>
              </li>
            ))}
          </ul>
        </div>
      </section>

      <div className="mb-6 grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Stat label="сыграно" value={`${data.questions_total}`} />
        <Stat label="взято" value={`${data.took}`} />
        <Stat label="ожидание поля" value={`${data.expected?.toFixed(0)}`} />
        <Stat label="дефицит" value={signed(deficit, 0)} tone={deltaClass(deficit)} />
      </div>

      <section className="mb-6">
        <div className="mb-2 flex items-center gap-1.5">
          <TrendingDown className="size-4 text-amber-ink" aria-hidden />
          <h2 className="text-sm font-semibold">Где теряем очки</h2>
        </div>
        <p className="mb-3 text-2xs text-muted-foreground">
          Отклонение от поля по полосам сложности. На лёгких вопросах команда не
          сыпется, на трудных их не берёт никто — весь недобор в «средних».
        </p>
        <Calibration bands={data.calibration ?? []} focus={data.focus_band} />
        {data.focus_band && (
          <div className="mt-3 flex flex-wrap items-center gap-2 rounded-lg border border-amber-ink/20 bg-amber-wash/40 px-3 py-2">
            <Target className="size-4 text-amber-ink" aria-hidden />
            <span className="text-xs">
              Зона фокуса тренировок: <b>{pct(data.focus_band[0])}–{pct(data.focus_band[1])}</b> беручести
            </span>
            <Button
              className="ml-auto h-7 text-2xs"
              onClick={() => startGap.mutate()}
              disabled={startGap.isPending}
            >
              Тренировать слабые темы
            </Button>
          </div>
        )}
      </section>

      <section className="mb-6">
        <div className="mb-2 flex items-center gap-1.5">
          <Crosshair className="size-4 text-amber-ink" aria-hidden />
          <h2 className="text-sm font-semibold">Прогноз счёта на пакет</h2>
        </div>
        <Forecast />
      </section>

      <section className="mb-6">
        <div className="mb-2 flex items-center gap-1.5">
          <Trophy className="size-4 text-amber-ink" aria-hidden />
          <h2 className="text-sm font-semibold">История турниров</h2>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-left text-2xs uppercase tracking-wide text-muted-foreground">
                <th className="py-1.5 pr-2 font-medium">Турнир</th>
                <th className="py-1.5 pr-2 text-right font-medium">Взято</th>
                <th className="py-1.5 pr-2 text-right font-medium">Поле</th>
                <th className="py-1.5 text-right font-medium">Дефицит</th>
              </tr>
            </thead>
            <tbody>
              {(data.tournaments ?? []).map((t, i) => (
                <tr key={i} className="border-t border-border">
                  <td className="py-1.5 pr-2">
                    <span className="line-clamp-1">{t.title}</span>
                    <span className="text-2xs text-muted-foreground">{t.date}</span>
                  </td>
                  <td className="py-1.5 pr-2 text-right tabular">{t.score}/{t.questions}</td>
                  <td className="py-1.5 pr-2 text-right tabular">{t.expected?.toFixed(1)}</td>
                  <td className={`py-1.5 text-right tabular font-medium ${deltaClass(t.deficit ?? 0)}`}>
                    {signed(t.deficit ?? 0)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {reliableCats.length > 0 && (
        <section className="mb-4">
          <h2 className="mb-2 text-sm font-semibold">Слабые темы (n≥5)</h2>
          <p className="mb-2 text-2xs text-muted-foreground">
            На {data.matched_questions} вопросах, где известна классификация.
          </p>
          <ul className="divide-y divide-border border-y border-border">
            {reliableCats.map((c) => (
              <li key={c.category} className="flex items-center justify-between gap-2 py-1.5 text-xs">
                <span>{c.category}</span>
                <span className="flex items-center gap-3 text-2xs text-muted-foreground">
                  <span className="tabular">{c.took}/{c.questions}</span>
                  <span className={`tabular font-medium ${deltaClass(c.per_question)}`}>
                    {signed(c.per_question, 2)}/в
                  </span>
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </Page>
  )
}
