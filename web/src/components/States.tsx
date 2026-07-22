import { AlertCircle, RotateCw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'

/** Пусто — объясняем причину и даём выход, а не просто «нет данных». */
export function Empty({
  title,
  hint,
  action,
  className,
}: {
  title: string
  hint?: string
  action?: React.ReactNode
  className?: string
}) {
  return (
    <div
      className={cn(
        'rounded-lg border border-dashed border-border bg-paper-raised/50 px-6 py-7 text-center',
        className,
      )}
    >
      <p className="text-sm font-medium text-foreground">{title}</p>
      {hint && (
        <p className="mx-auto mt-1.5 max-w-[46ch] text-xs leading-relaxed text-muted-foreground">
          {hint}
        </p>
      )}
      {action && <div className="mt-4">{action}</div>}
    </div>
  )
}

/** Бэкенд недоступен: запрос «на паузе», а не просто с ошибкой. */
export class ConnectionError extends Error {}

/** Ошибка загрузки с учётом «паузы» React Query.
 *
 * Когда бэкенд недоступен, запрос уходит в fetchStatus: 'paused', error
 * остаётся null, а status навсегда 'pending'. Без этой проверки страница
 * показывала бы «пусто» (то есть врала, что база не классифицирована)
 * или вечный скелет. Пауза означает «не дотянуться до сети» — так и пишем.
 */
export function loadError(error: unknown, fetchStatus: string): unknown | null {
  if (error) return error
  if (fetchStatus === 'paused') {
    return new ConnectionError(
      'Нет связи с сервером. Проверьте, запущен ли API: .venv/bin/uvicorn api.main:app --port 8000',
    )
  }
  return null
}

export function ErrorState({
  error,
  onRetry,
}: {
  error: unknown
  onRetry?: () => void
}) {
  const message = error instanceof Error ? error.message : 'Неизвестная ошибка'
  // Приостановленный запрос не возобновляется через refetch(), поэтому
  // единственное честное действие здесь — перезагрузить страницу.
  const offline = error instanceof ConnectionError

  return (
    <div
      role="alert"
      className="flex items-start gap-3 rounded-lg border border-missed/30 bg-missed-wash px-4 py-3.5"
    >
      <AlertCircle className="mt-0.5 size-4 shrink-0 text-missed" strokeWidth={2} aria-hidden />
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium text-missed">
          {offline ? 'Сервер недоступен' : 'Не удалось загрузить'}
        </p>
        <p className="mt-0.5 text-xs break-words text-missed/80">{message}</p>
      </div>
      {(onRetry || offline) && (
        <Button
          variant="outline"
          size="sm"
          className="shrink-0"
          onClick={offline ? () => window.location.reload() : onRetry}
        >
          <RotateCw className="size-3" aria-hidden />
          {offline ? 'Обновить' : 'Ещё раз'}
        </Button>
      )}
    </div>
  )
}

/** Скелет строки каталога — совпадает по высоте с реальной строкой. */
export function RowSkeleton({ count = 8 }: { count?: number }) {
  return (
    <div className="divide-y divide-border" aria-hidden>
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className="space-y-2 px-3 py-3.5">
          <div className="flex gap-2">
            <Skeleton className="h-3 w-12" />
            <Skeleton className="h-3 w-20" />
          </div>
          <Skeleton className="h-3.5 w-full" />
          <Skeleton className="h-3.5 w-3/5" />
        </div>
      ))}
    </div>
  )
}

export function BlockSkeleton({ className }: { className?: string }) {
  return <Skeleton className={cn('h-24 w-full', className)} />
}
