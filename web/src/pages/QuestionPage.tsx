import { useEffect, useState } from 'react'
import { useParams, useNavigate, Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { ArrowLeft, Check, X, ExternalLink } from 'lucide-react'
import { api } from '@/lib/api'
import { Page } from '@/components/AppShell'
import { ErrorState, BlockSkeleton, loadError } from '@/components/States'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { fmtDateTime, fmtTime, modeLabel, LEITNER_DAYS } from '@/lib/format'
import { cn } from '@/lib/utils'

/** Показывать ли ответ сразу. Выбор живёт между визитами: одни ходят в
 *  картотеку думать над вопросом, другие — смотреть ответ. */
const REVEAL_KEY = 'chgk.alwaysRevealAnswer'

function readAlwaysReveal(): boolean {
  try {
    return localStorage.getItem(REVEAL_KEY) === '1'
  } catch {
    return false
  }
}

/** Строка метаданных: подпись слева, значение справа. Плотно, без карточек. */
function MetaRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-3 py-1.5">
      <dt className="w-24 shrink-0 text-2xs text-muted-foreground">{label}</dt>
      <dd className="min-w-0 flex-1 text-xs">{children}</dd>
    </div>
  )
}

function Block({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mt-6">
      <h2 className="mb-2 text-xs font-medium tracking-wide text-muted-foreground uppercase">
        {title}
      </h2>
      {children}
    </section>
  )
}

export function QuestionPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const questionId = Number(id)

  const [alwaysReveal, setAlwaysReveal] = useState(readAlwaysReveal)
  const [revealed, setRevealed] = useState(alwaysReveal)

  // Новый вопрос — снова прячем ответ, если только не выбрано «всегда».
  useEffect(() => {
    setRevealed(alwaysReveal)
  }, [questionId, alwaysReveal])

  useEffect(() => {
    try {
      localStorage.setItem(REVEAL_KEY, alwaysReveal ? '1' : '0')
    } catch {
      /* приватный режим — просто не запоминаем */
    }
  }, [alwaysReveal])

  const { data: q, isPending, error, fetchStatus, refetch } = useQuery({
    queryKey: ['question', questionId],
    queryFn: () => api.question(questionId),
    enabled: Number.isFinite(questionId),
  })

  const { data: similar } = useQuery({
    queryKey: ['similar', questionId],
    queryFn: () => api.similar(questionId, 8),
    enabled: Number.isFinite(questionId),
  })

  const err = loadError(error, fetchStatus)

  if (err) {
    return (
      <Page>
        <ErrorState error={err} onRetry={() => refetch()} />
      </Page>
    )
  }

  if (isPending || !q) {
    return (
      <Page>
        <BlockSkeleton className="h-6 w-32" />
        <BlockSkeleton className="mt-6 h-32" />
        <BlockSkeleton className="mt-6 h-24" />
      </Page>
    )
  }

  const razdatkaPic = q.razdatka_pic
    ? q.razdatka_pic.startsWith('http')
      ? q.razdatka_pic
      : `https://gotquestions.online${q.razdatka_pic}`
    : null

  return (
    <Page>
      <div className="mb-5 flex items-center gap-3">
        <Button variant="ghost" size="sm" onClick={() => navigate(-1)}>
          <ArrowLeft className="size-3.5" aria-hidden />
          Назад
        </Button>
        <span className="tabular text-2xs text-muted-foreground">Вопрос #{q.id}</span>
      </div>

      {/* ─── Раздатка ──────────────────────────────────────────── */}
      {(q.razdatka_text || razdatkaPic) && (
        <div className="mb-5 rounded-lg border border-amber/40 bg-amber-wash/40 px-4 py-3">
          <p className="mb-1.5 text-2xs font-medium tracking-wide text-amber-ink uppercase">
            Раздатка
          </p>
          {q.razdatka_text && (
            <p className="prose-question text-[15px] whitespace-pre-wrap">{q.razdatka_text}</p>
          )}
          {razdatkaPic && (
            <img
              src={razdatkaPic}
              alt="Раздаточный материал"
              loading="lazy"
              className="mt-2 max-h-80 rounded-md border border-border"
            />
          )}
        </div>
      )}

      {/* ─── Вопрос ────────────────────────────────────────────── */}
      <p className="question-text whitespace-pre-wrap">{q.text}</p>

      {/* ─── Ответ ─────────────────────────────────────────────── */}
      {/* По умолчанию скрыт: увидеть ответ случайно нельзя развидеть, а лишний
          клик стоит недорого. Кто ходит в картотеку за ответами, снимает это
          один раз — выбор запоминается. */}
      {!revealed ? (
        <div className="mt-6 flex flex-wrap items-center gap-3 border-t border-border pt-5">
          <Button onClick={() => setRevealed(true)} className="h-9">
            Показать ответ
          </Button>
          <label className="flex cursor-pointer items-center gap-2 text-2xs text-muted-foreground">
            <Checkbox
              checked={alwaysReveal}
              onCheckedChange={(v) => setAlwaysReveal(v === true)}
            />
            Всегда показывать сразу
          </label>
        </div>
      ) : (
        <>
          <div className="mt-6 border-l-2 border-knew pl-4">
            <p className="mb-1 text-2xs font-medium tracking-wide text-knew uppercase">Ответ</p>
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

          {q.nezachet && (
            <div className="mt-3 border-l-2 border-missed/50 pl-4">
              <p className="mb-1 text-2xs font-medium tracking-wide text-missed uppercase">
                Незачёт
              </p>
              <p className="prose-question text-[15px] whitespace-pre-wrap">{q.nezachet}</p>
            </div>
          )}

          {q.comment && (
            <Block title="Комментарий">
              <p className="prose-question text-[15px] whitespace-pre-wrap text-foreground/90">
                {q.comment}
              </p>
            </Block>
          )}
        </>
      )}

      {/* ─── Паспорт вопроса ───────────────────────────────────── */}
      <Block title="Источник и метаданные">
        <dl className="divide-y divide-border border-y border-border">
          {/* Источник прячем вместе с ответом: он спойлерит не хуже —
              «Борис Акунин „Яма“» рядом со скрытым ответом бессмысленно. */}
          {q.source && revealed && (
            <MetaRow label="Источник">
              <span className="break-words whitespace-pre-wrap">{q.source}</span>
            </MetaRow>
          )}
          {q.authors.length > 0 && (
            <MetaRow label="Автор">{q.authors.join(', ')}</MetaRow>
          )}
          {q.pack_title && (
            <MetaRow label="Турнир">
              <span>{q.pack_title}</span>
              {q.year && <span className="tabular text-muted-foreground"> · {q.year}</span>}
              {q.tour_number && (
                <span className="text-muted-foreground"> · тур {q.tour_number}</span>
              )}
              {q.number && <span className="text-muted-foreground"> · вопрос {q.number}</span>}
            </MetaRow>
          )}
          {q.question_difficulty !== null && q.question_difficulty !== undefined && (
            <MetaRow label="Беручесть">
              <span className="tabular">
                {Math.round((10 - q.question_difficulty) * 10)}%
              </span>
              <span className="text-muted-foreground"> · взяли команд на турнире</span>
            </MetaRow>
          )}
          {q.pack_difficulty !== null && (
            <MetaRow label="Сложность">
              <span className="tabular">{q.pack_difficulty.toFixed(2)}</span>
              <span className="text-muted-foreground"> · trueDL всего пакета</span>
            </MetaRow>
          )}
          {q.topics.length > 0 && (
            <MetaRow label="Темы">
              <div className="flex flex-wrap gap-x-3 gap-y-1">
                {q.topics.map((t, i) => (
                  <span key={i}>
                    <span className="text-amber-ink">{t.category}</span>
                    <span className="text-muted-foreground"> · {t.subcategory}</span>
                    {t.confidence !== null && (
                      <span className="tabular text-muted-foreground/70">
                        {' '}
                        {Math.round(t.confidence * 100)}%
                      </span>
                    )}
                  </span>
                ))}
              </div>
            </MetaRow>
          )}
          {q.pack_link && (
            <MetaRow label="Ссылка">
              <a
                href={q.pack_link}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1 text-amber-ink underline-offset-2 hover:underline"
              >
                Открыть на gotquestions.online
                <ExternalLink className="size-3" aria-hidden />
              </a>
            </MetaRow>
          )}
        </dl>
      </Block>

      {/* ─── История попыток ───────────────────────────────────── */}
      <Block title="История">
        {q.attempts.length === 0 ? (
          <p className="text-xs text-muted-foreground">
            Вопрос ещё не встречался в тренировках.
          </p>
        ) : (
          <>
            {q.leitner && (
              <p className="mb-2.5 text-2xs text-muted-foreground">
                Коробка {q.leitner.box} из 5 · интервал {LEITNER_DAYS[q.leitner.box]}{' '}
                дн. · следующее повторение {fmtDateTime(q.leitner.next_review_at)}
              </p>
            )}
            <ul className="divide-y divide-border border-y border-border">
              {q.attempts.map((a, i) => (
                <li key={i} className="flex items-center gap-3 py-2">
                  {a.knew ? (
                    <Check className="size-3.5 shrink-0 text-knew" strokeWidth={2.5} aria-hidden />
                  ) : (
                    <X className="size-3.5 shrink-0 text-missed" strokeWidth={2.5} aria-hidden />
                  )}
                  <span className="tabular w-28 shrink-0 text-2xs text-muted-foreground">
                    {fmtDateTime(a.attempted_at)}
                  </span>
                  <span
                    className={cn(
                      'min-w-0 flex-1 truncate text-xs',
                      a.user_answer ? 'text-foreground' : 'text-muted-foreground/60 italic',
                    )}
                  >
                    {a.user_answer || 'без ответа'}
                  </span>
                  <span className="text-2xs text-muted-foreground">{modeLabel(a.mode)}</span>
                  <span className="tabular w-10 text-right text-2xs text-muted-foreground">
                    {fmtTime(a.time_seconds)}
                  </span>
                </li>
              ))}
            </ul>
          </>
        )}
      </Block>

      {/* ─── Похожие вопросы (эмбеддинги) ──────────────────────── */}
      {/* Ответы соседей спойлерят не хуже своего: прячем их за тем же
          revealed, что и основной ответ. */}
      {similar?.available && similar.items.length > 0 && (
        <Block title="Похожие вопросы">
          <ul className="divide-y divide-border border-y border-border">
            {similar.items.map((it) => (
              <li key={it.id} className="py-2.5">
                <Link to={`/question/${it.id}`} className="group block">
                  <div className="mb-0.5 flex items-center gap-2 text-2xs text-muted-foreground">
                    <span className="tabular rounded bg-amber-wash/60 px-1.5 py-0.5 text-amber-ink">
                      Сходство {Math.round(it.similarity * 100)}%
                    </span>
                    {it.question_difficulty !== null && (
                      <span className="tabular text-muted-foreground">
                        Взяли {Math.round((10 - it.question_difficulty) * 10)}%
                      </span>
                    )}
                    {it.similarity >= 0.95 && (
                      <span className="text-missed">почти дубль</span>
                    )}
                    {it.category && <span className="text-amber-ink">{it.category}</span>}
                    {it.pack_title && (
                      <span className="truncate">
                        {it.pack_title}
                        {it.year && ` · ${it.year}`}
                      </span>
                    )}
                  </div>
                  <p className="text-xs leading-relaxed group-hover:text-amber-ink">
                    {it.text_preview}…
                  </p>
                  {revealed && (
                    <p className="mt-0.5 text-2xs text-muted-foreground">
                      Ответ: {it.answer}
                    </p>
                  )}
                </Link>
              </li>
            ))}
          </ul>
        </Block>
      )}

      {q.category && (
        <div className="mt-6">
          <Link
            to={`/catalog?category=${encodeURIComponent(q.category)}`}
            className="text-xs text-amber-ink underline-offset-2 hover:underline"
          >
            Другие вопросы по теме «{q.category}» →
          </Link>
        </div>
      )}
    </Page>
  )
}
