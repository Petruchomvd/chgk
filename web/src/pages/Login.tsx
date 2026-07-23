import { useState, type FormEvent } from 'react'
import {
  ArrowRight,
  Eye,
  EyeOff,
} from 'lucide-react'
import { ApiError, api, type AuthSession } from '@/lib/api'
import { cn } from '@/lib/utils'

export function Login({
  onSuccess,
}: {
  onSuccess: (session: AuthSession) => void
}) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [remember, setRemember] = useState(true)
  const [showPassword, setShowPassword] = useState(false)
  const [error, setError] = useState('')
  const [pending, setPending] = useState(false)

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError('')
    setPending(true)
    try {
      onSuccess(await api.login(username, password, remember))
    } catch (cause) {
      setError(
        cause instanceof ApiError
          ? cause.message
          : 'Не удалось связаться с сервером. Попробуйте ещё раз.',
      )
    } finally {
      setPending(false)
    }
  }

  return (
    <main className="login-page">
      <section className="login-story" aria-label="О пространстве">
        <div className="login-grain" aria-hidden />
        <div className="relative z-10 flex h-full max-w-2xl flex-col">
          <div className="flex items-center gap-3">
            <div className="login-mark" aria-hidden>
              <span />
              <span />
              <span />
            </div>
            <div>
              <div className="font-serif text-lg font-semibold tracking-tight text-paper">
                Картотека
              </div>
              <div className="text-2xs tracking-[0.18em] text-ink-muted uppercase">
                Что? Где? Когда?
              </div>
            </div>
          </div>

          <div className="login-symbol my-auto" aria-hidden>
            ?
          </div>

          <div>
            <div className="font-serif text-xl font-semibold text-paper">
              База вопросов
            </div>
            <div className="mt-1.5 text-xs text-ink-muted">
              Тренировки и прогресс
            </div>
          </div>
        </div>
      </section>

      <section className="login-form-panel">
        <div className="w-full max-w-[410px]">
          <div className="mb-8 lg:hidden">
            <div className="font-serif text-lg font-semibold text-ink">Картотека</div>
            <div className="mt-1 text-2xs tracking-[0.16em] text-muted-foreground uppercase">
              Что? Где? Когда?
            </div>
          </div>

          <div className="mb-9">
            <h2 className="font-serif text-[30px] leading-tight font-semibold tracking-tight text-foreground">
              Вход
            </h2>
            <p className="mt-2 text-sm leading-6 text-muted-foreground">
              Введите логин и пароль.
            </p>
          </div>

          <form onSubmit={submit} className="space-y-5">
            <label className="block">
              <span className="mb-2 block text-xs font-medium text-foreground">Логин</span>
              <input
                autoFocus
                autoComplete="username"
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                className="login-input"
                placeholder="Ваш логин"
                required
              />
            </label>

            <label className="block">
              <span className="mb-2 block text-xs font-medium text-foreground">Пароль</span>
              <span className="relative block">
                <input
                  type={showPassword ? 'text' : 'password'}
                  autoComplete="current-password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  className="login-input pr-12"
                  placeholder="Ваш пароль"
                  required
                />
                <button
                  type="button"
                  onClick={() => setShowPassword((value) => !value)}
                  className="absolute inset-y-0 right-0 flex w-11 items-center justify-center text-muted-foreground transition-colors hover:text-foreground"
                  aria-label={showPassword ? 'Скрыть пароль' : 'Показать пароль'}
                >
                  {showPassword ? (
                    <EyeOff className="size-4" strokeWidth={1.7} />
                  ) : (
                    <Eye className="size-4" strokeWidth={1.7} />
                  )}
                </button>
              </span>
            </label>

            <label className="flex cursor-pointer items-center gap-2.5 text-xs text-muted-foreground">
              <input
                type="checkbox"
                checked={remember}
                onChange={(event) => setRemember(event.target.checked)}
                className="login-checkbox"
              />
              Не выходить на этом устройстве
            </label>

            {error ? (
              <div
                role="alert"
                className="rounded-md border border-missed/25 bg-missed-wash px-3.5 py-3 text-xs leading-5 text-missed"
              >
                {error}
              </div>
            ) : null}

            <button
              type="submit"
              disabled={pending}
              className={cn('login-submit', pending && 'cursor-wait opacity-70')}
            >
              <span>{pending ? 'Проверяем…' : 'Войти'}</span>
              <ArrowRight className="size-4" strokeWidth={1.8} />
            </button>
          </form>

        </div>
      </section>
    </main>
  )
}
