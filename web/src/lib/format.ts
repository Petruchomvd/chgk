/** Форматирование: русские числительные, время, даты. */

export function plural(n: number, one: string, few: string, many: string): string {
  const mod10 = n % 10
  const mod100 = n % 100
  if (mod10 === 1 && mod100 !== 11) return one
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20)) return few
  return many
}

export function questionsWord(n: number): string {
  return plural(n, 'вопрос', 'вопроса', 'вопросов')
}

/** Согласование сказуемого: «1 вопрос ждёт», но «2 вопроса ждут». */
export function verbAgrees(n: number, singular: string, pluralForm: string): string {
  return plural(n, singular, pluralForm, pluralForm)
}

/** 212779 → «212 779» (неразрывные пробелы, чтобы число не рвалось). */
export function num(n: number | null | undefined): string {
  if (n === null || n === undefined) return '—'
  return n.toLocaleString('ru-RU').replace(/\s/g, ' ')
}

export function fmtTime(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return '—'
  const s = Math.max(0, Math.round(seconds))
  const m = Math.floor(s / 60)
  return `${m}:${String(s % 60).padStart(2, '0')}`
}

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' })
}

export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString('ru-RU', {
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  })
}

/** «сегодня» / «вчера» / «3 дня назад» — для истории попыток. */
export function relativeDay(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const today = new Date()
  const startOf = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime()
  const days = Math.round((startOf(today) - startOf(d)) / 86_400_000)
  if (days === 0) return 'сегодня'
  if (days === 1) return 'вчера'
  if (days < 7) return `${days} ${plural(days, 'день', 'дня', 'дней')} назад`
  return fmtDate(iso)
}

const MODE_LABELS: Record<string, string> = {
  random: 'случайные',
  category: 'по темам',
  tournament: 'турнир',
  review: 'повторение',
}

export function modeLabel(mode: string | null | undefined): string {
  if (!mode) return '—'
  return MODE_LABELS[mode] ?? mode
}

/** Интервалы Leitner — те же, что в database/training_db.py. */
export const LEITNER_DAYS: Record<number, number> = { 1: 1, 2: 3, 3: 7, 4: 14, 5: 30 }
