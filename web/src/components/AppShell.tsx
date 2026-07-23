import { NavLink, useLocation } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import {
  LayoutList,
  Library,
  Layers,
  Dumbbell,
  RotateCcw,
  Sparkles,
  Compass,
  Target,
  GraduationCap,
  LogOut,
  Menu,
  X,
} from 'lucide-react'
import { api } from '@/lib/api'
import { num } from '@/lib/format'
import { cn } from '@/lib/utils'
import { useAuth } from '@/auth/AuthContext'

interface NavItem {
  to: string
  label: string
  icon: typeof Library
  /** Счётчик показываем только там, где он влияет на решение начать работу. */
  badge?: (due: number) => number | null
}

const PRIMARY_NAV: NavItem[] = [
  { to: '/training', label: 'Тренировка', icon: Dumbbell },
  { to: '/review', label: 'Повторение', icon: RotateCcw, badge: (d) => d || null },
  { to: '/study', label: 'Учить', icon: GraduationCap },
  { to: '/', label: 'Мой прогресс', icon: LayoutList },
  { to: '/catalog', label: 'Картотека', icon: Library },
]

const OWNER_NAV: NavItem[] = [
  { to: '/team', label: 'Команда', icon: Target },
  { to: '/topics', label: 'Темы базы', icon: Layers },
  { to: '/map', label: 'Смысловая карта', icon: Compass },
  { to: '/search', label: 'Поиск по смыслу', icon: Sparkles },
]

function DueBadge({ value, dark }: { value: number; dark?: boolean }) {
  return (
    <span
      className={cn(
        'tabular ml-auto rounded-sm px-1.5 py-0.5 text-2xs font-medium',
        dark
          ? 'bg-amber/20 text-amber-soft'
          : 'bg-amber-wash text-amber-ink',
      )}
    >
      {value}
    </span>
  )
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const { user, logout } = useAuth()
  const [mobileMenu, setMobileMenu] = useState(false)
  const { data: overview } = useQuery({
    queryKey: ['overview'],
    queryFn: api.overview,
    staleTime: 30_000,
  })
  const { data: meta } = useQuery({
    queryKey: ['meta'],
    queryFn: api.meta,
    staleTime: Infinity,
  })
  const location = useLocation()
  const due = overview?.due_count ?? 0
  const ownerNav = OWNER_NAV.filter((item) => {
    if (item.to === '/map') return meta?.features.semantic_map
    if (item.to === '/search') return meta?.features.semantic_search
    return true
  })

  // Тренировка — режим фокуса: скрываем всю навигацию.
  const focusMode = /^\/training\/[^/]+$/.test(location.pathname)

  if (focusMode) return <>{children}</>

  return (
    <div className="min-h-screen lg:flex">
      {/* ─── Боковая панель (десктоп) ───────────────────────────── */}
      <aside
        className="sticky top-0 hidden h-screen w-56 shrink-0 flex-col
                   border-r border-ink-line bg-ink lg:flex"
      >
        <div className="border-b border-ink-line px-5 py-4">
          <div className="font-serif text-[15px] leading-none font-semibold text-paper">
            Картотека
          </div>
          <div className="mt-1.5 text-2xs tracking-wide text-ink-muted uppercase">
            Что? Где? Когда?
          </div>
        </div>

        <nav className="flex-1 px-2 py-3" aria-label="Основная навигация">
          <ul className="space-y-0.5">
            {PRIMARY_NAV.map(({ to, label, icon: Icon, badge }) => {
              const count = badge?.(due) ?? null
              return (
                <li key={to}>
                  <NavLink
                    to={to}
                    end={to === '/'}
                    className={({ isActive }) =>
                      cn(
                        'group flex items-center gap-2.5 rounded-md px-3 py-2 text-[13px] transition-colors',
                        // Активный раздел — янтарная метка слева, не заливка.
                        isActive
                          ? 'bg-ink-soft font-medium text-paper shadow-[inset_2px_0_0_0_var(--amber)]'
                          : 'text-ink-muted hover:bg-ink-soft/60 hover:text-paper',
                      )
                    }
                  >
                    <Icon className="size-4 shrink-0" strokeWidth={1.75} aria-hidden />
                    {label}
                    {count ? <DueBadge value={count} dark /> : null}
                  </NavLink>
                </li>
              )
            })}
          </ul>
          {user.role === 'owner' && (
            <>
              <div className="mx-3 mt-5 mb-2 text-[10px] font-medium tracking-[0.12em] text-ink-muted/80 uppercase">
                Управление
              </div>
              <ul className="space-y-0.5">
                {ownerNav.map(({ to, label, icon: Icon }) => (
                  <li key={to}>
                    <NavLink
                      to={to}
                      className={({ isActive }) =>
                        cn(
                          'group flex items-center gap-2.5 rounded-md px-3 py-2 text-[13px] transition-colors',
                          isActive
                            ? 'bg-ink-soft font-medium text-paper shadow-[inset_2px_0_0_0_var(--amber)]'
                            : 'text-ink-muted hover:bg-ink-soft/60 hover:text-paper',
                        )
                      }
                    >
                      <Icon className="size-4 shrink-0" strokeWidth={1.75} aria-hidden />
                      {label}
                    </NavLink>
                  </li>
                ))}
              </ul>
            </>
          )}
        </nav>

        {/* Состояние базы — постоянный контекст, а не украшение. */}
        <div className="border-t border-ink-line">
          {meta && (
            <div className="px-5 py-3 text-2xs text-ink-muted">
              <div className="tabular flex justify-between">
                <span>Вопросов</span>
                <span className="text-paper/80">{num(meta.total_questions)}</span>
              </div>
              <div className="tabular mt-1 flex justify-between">
                <span>Размечено</span>
                <span className="text-paper/80">{meta.classification_pct}%</span>
              </div>
            </div>
          )}
          <div className="flex items-center gap-2 border-t border-ink-line px-3 py-2.5">
            <div className="flex size-7 shrink-0 items-center justify-center rounded-sm bg-amber/15 text-xs font-semibold text-amber-soft">
              {user.display_name.slice(0, 1).toUpperCase()}
            </div>
            <div className="min-w-0 flex-1">
              <div className="truncate text-xs font-medium text-paper">{user.display_name}</div>
              <div className="truncate text-2xs text-ink-muted">@{user.username}</div>
            </div>
            <button
              onClick={() => void logout()}
              className="flex size-7 items-center justify-center rounded-sm text-ink-muted transition-colors hover:bg-ink-soft hover:text-paper"
              aria-label="Выйти"
              title="Выйти"
            >
              <LogOut className="size-3.5" strokeWidth={1.7} />
            </button>
          </div>
        </div>
      </aside>

      {/* ─── Мобильная шапка ────────────────────────────────────── */}
      <header
        className="sticky top-0 z-20 flex items-center gap-3 border-b border-ink-line
                   bg-ink px-4 py-2.5 lg:hidden"
      >
        <span className="font-serif text-sm font-semibold text-paper">Картотека</span>
        <span className="text-2xs tracking-wide text-ink-muted uppercase">ЧГК</span>
        <span className="ml-auto text-2xs text-ink-muted">{user.display_name}</span>
        {user.role === 'owner' && (
          <button
            type="button"
            onClick={() => setMobileMenu((value) => !value)}
            className="flex size-9 items-center justify-center rounded-md text-ink-muted hover:bg-ink-soft hover:text-paper"
            aria-expanded={mobileMenu}
            aria-label={mobileMenu ? 'Закрыть меню управления' : 'Открыть меню управления'}
          >
            {mobileMenu ? <X className="size-4" /> : <Menu className="size-4" />}
          </button>
        )}
        <button
          onClick={() => void logout()}
          className="text-ink-muted"
          aria-label="Выйти"
        >
          <LogOut className="size-4" strokeWidth={1.7} />
        </button>
      </header>

      {mobileMenu && user.role === 'owner' && (
        <div className="fixed inset-x-0 top-[49px] z-30 border-b border-ink-line bg-ink px-3 py-3 shadow-lg lg:hidden">
          <div className="mb-2 px-2 text-[10px] font-medium tracking-[0.12em] text-ink-muted uppercase">
            Управление
          </div>
          <div className="grid grid-cols-2 gap-1">
            {ownerNav.map(({ to, label, icon: Icon }) => (
              <NavLink
                key={to}
                to={to}
                onClick={() => setMobileMenu(false)}
                className={({ isActive }) =>
                  cn(
                    'flex min-h-11 items-center gap-2 rounded-md px-3 text-xs',
                    isActive ? 'bg-ink-soft text-paper' : 'text-ink-muted',
                  )
                }
              >
                <Icon className="size-4" aria-hidden />
                {label}
              </NavLink>
            ))}
          </div>
        </div>
      )}

      <main className="min-w-0 flex-1 pb-16 lg:pb-0">{children}</main>

      {/* ─── Мобильная навигация ────────────────────────────────── */}
      <nav
        className="fixed inset-x-0 bottom-0 z-20 grid grid-cols-5 border-t border-ink-line
                   bg-ink lg:hidden"
        aria-label="Основная навигация"
      >
        {PRIMARY_NAV.map(({ to, label, icon: Icon, badge }) => {
          const count = badge?.(due) ?? null
          return (
            <NavLink
              key={to}
              to={to}
              end={to === '/'}
              className={({ isActive }) =>
                cn(
                  'relative flex min-h-14 flex-col items-center justify-center gap-1 px-1 py-1.5 text-[10px] transition-colors',
                  isActive ? 'text-amber-soft' : 'text-ink-muted',
                )
              }
            >
              <Icon className="size-[18px]" strokeWidth={1.75} aria-hidden />
              {label}
              {count ? (
                <span
                  className="tabular absolute top-1 right-[22%] rounded-sm bg-amber px-1
                             text-[9px] font-medium text-ink"
                >
                  {count}
                </span>
              ) : null}
            </NavLink>
          )
        })}
      </nav>
    </div>
  )
}

/** Общая рамка страницы: единые поля и максимальная ширина. */
export function Page({
  children,
  className,
  wide,
}: {
  children: React.ReactNode
  className?: string
  wide?: boolean
}) {
  return (
    <div
      className={cn(
        'mx-auto px-4 py-6 sm:px-6 lg:px-8 lg:py-8',
        wide ? 'max-w-[1180px]' : 'max-w-[880px]',
        className,
      )}
    >
      {children}
    </div>
  )
}

/** Заголовок страницы: имя слева, действия справа. Без hero-блоков. */
export function PageHeader({
  title,
  meta,
  actions,
}: {
  title: string
  meta?: React.ReactNode
  actions?: React.ReactNode
}) {
  return (
    <div className="mb-5 flex flex-wrap items-baseline justify-between gap-x-4 gap-y-2">
      <div className="flex items-baseline gap-3">
        <h1 className="font-serif text-[19px] font-semibold tracking-tight">{title}</h1>
        {meta && <span className="text-xs text-muted-foreground">{meta}</span>}
      </div>
      {actions}
    </div>
  )
}
