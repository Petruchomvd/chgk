/** Клиент API. Типы отражают ответы `api/main.py`. */

/** get_all_categories() отдаёт только id и name_ru. */
export interface Category {
  id: number
  name_ru: string
}

export interface Meta {
  categories: Category[]
  models: string[]
  total_questions: number
  total_packs: number
  classified: number
  classification_pct: number
  with_difficulty: number
  features: {
    semantic_map: boolean
    semantic_search: boolean
    fact_cards: boolean
  }
}

export interface CatalogItem {
  id: number
  text: string
  text_preview: string
  answer: string
  authors: string[]
  tour_number: number | null
  number: number | null
  category_id: number | null
  category: string | null
  subcategory: string | null
  confidence: number | null
  pack_id: number | null
  pack_title: string | null
  pack_difficulty: number | null
  question_difficulty: number | null
  year: string | null
  attempts_count: number
  knew_any: number | null
  last_attempt_at: string | null
  is_due: number
}

export interface CatalogPage {
  items: CatalogItem[]
  total: number
  limit: number
  offset: number
}

export interface Attempt {
  attempted_at: string
  user_answer: string | null
  knew: number
  time_seconds: number | null
  mode: string | null
}

export interface QuestionDetail {
  id: number
  text: string
  answer: string
  zachet: string | null
  nezachet: string | null
  comment: string | null
  source: string | null
  authors: string[]
  razdatka_text: string | null
  razdatka_pic: string | null
  number: number | null
  tour_number: number | null
  question_difficulty: number | null
  category: string | null
  subcategory: string | null
  confidence: number | null
  pack_id: number | null
  pack_title: string | null
  pack_link: string | null
  pack_difficulty: number | null
  year: string | null
  topics: {
    category: string
    subcategory: string
    confidence: number | null
    model_name: string | null
  }[]
  attempts: Attempt[]
  leitner: {
    box: number
    next_review_at: string
    consecutive_correct: number
  } | null
}

export interface TopicCategory {
  category_id: number
  category: string
  questions_count: number
  attempts_count: number
  distinct_questions: number
  success_pct: number | null
  subcategories: {
    subcategory_id: number
    subcategory: string
    questions_count: number
  }[]
}

export interface TrainingQuestion {
  id: number
  text: string
  answer: string
  zachet?: string | null
  nezachet?: string | null
  comment?: string | null
  source?: string | null
  authors?: string[]
  razdatka_text?: string | null
  razdatka_pic?: string | null
  pack_title?: string | null
  pack_difficulty?: number | null
  question_difficulty?: number | null
  pack_link?: string | null
  category?: string | null
  subcategory?: string | null
  confidence?: number | null
}

export interface SessionResult {
  question_id: number
  user_answer: string
  correct_answer: string
  knew: boolean
  time_seconds: number
  category: string | null
}

export interface Summary {
  total: number
  correct: number
  pct: number
  avg_time: number
  by_category: Record<string, { total: number; correct: number }>
  filters_repr: string
}

export interface TrainingState {
  session_id: string
  mode: string
  filters_repr: string
  index: number
  total: number
  finished: boolean
  question: TrainingQuestion | null
  elapsed: number
  results: SessionResult[]
  summary: Summary | null
}

export interface Overview {
  due_count: number
  /** Ключи — как в database/training_db.get_stats(). */
  stats: {
    total_attempts: number
    correct_attempts: number
    distinct_questions: number
    due_now: number
    by_category: { category: string; total: number; knew: number }[]
    by_box: { box: number; c: number }[]
  }
  progress: {
    active_days_30: number
    current_streak: number
    recent_success_pct: number | null
    recent_sample: number
    recent_avg_seconds: number | null
  }
  base: {
    total_questions: number
    total_packs: number
    classified: number
    classification_pct: number
  }
  weak_categories: {
    category: string
    attempts_count: number
    knew_count: number
    success_pct: number
  }[]
  strong_categories: {
    category: string
    attempts_count: number
    knew_count: number
    success_pct: number
  }[]
  recent: {
    question_id: number
    attempted_at: string
    knew: number
    category: string | null
    mode: string | null
    text_preview: string
    answer: string
  }[]
  activity: { day: string; total: number; knew: number }[]
  active_session: TrainingState | null
}

export interface Tournament {
  id: number
  title: string
  difficulty: number | null
  questions_count: number
  year: string | null
}

export interface TournamentPage {
  items: Tournament[]
  total: number
  years: number[]
}

export class ApiError extends Error {
  status: number

  constructor(message: string, status: number) {
    super(message)
    this.status = status
  }
}

export interface AuthUser {
  id: number
  username: string
  display_name: string
  role: 'owner' | 'player'
}

export interface AuthSession {
  user: AuthUser
  csrf_token: string
}

let csrfToken = ''

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    credentials: 'same-origin',
    headers: {
      'Content-Type': 'application/json',
      ...(csrfToken ? { 'X-CSRF-Token': csrfToken } : {}),
      ...init?.headers,
    },
  })
  if (!res.ok) {
    let detail = `Ошибка ${res.status}`
    try {
      const body = await res.json()
      if (body?.detail) detail = body.detail
    } catch {
      /* тело не JSON — оставляем код */
    }
    if (res.status === 401 && !path.startsWith('/api/auth/')) {
      window.dispatchEvent(new Event('chgk:session-expired'))
    }
    throw new ApiError(detail, res.status)
  }
  return res.json() as Promise<T>
}

async function authRequest(
  path: string,
  init?: RequestInit,
): Promise<AuthSession> {
  const session = await request<AuthSession>(path, init)
  csrfToken = session.csrf_token
  return session
}

export interface CatalogFilters {
  search?: string
  category_id?: number | null
  subcategory_id?: number | null
  year_from?: number | null
  year_to?: number | null
  difficulty_min?: number | null
  difficulty_max?: number | null
  author?: string | null
  status?: string | null
  limit?: number
  offset?: number
  [key: string]: unknown
}

function qs(params: Record<string, unknown>): string {
  const sp = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== '') sp.set(k, String(v))
  }
  const s = sp.toString()
  return s ? `?${s}` : ''
}

export type WeakTopicCategory = {
  category: string
  category_id: number | null
  questions: number
  took: number
  expected: number
  deficit: number
  per_question: number
  weak: boolean
}

/** Слабости, измеренные на турнирах (scripts/team_gap.py), а не самооценкой. */
export type WeakTopics = {
  available: boolean
  team_title?: string
  questions_total?: number
  per_question_avg?: number
  tournaments?: { title: string; date: string; score: number; questions: number }[]
  categories: WeakTopicCategory[]
}

/** Досье команды: история + калибровка + модель (scripts/team_history.py). */
export interface CalibrationBand {
  band: [number, number]
  questions: number
  field_rate: number
  team_rate: number
  lift: number
  deficit: number
}

export interface TeamTournament {
  title: string
  date: string
  score: number
  questions: number
  expected?: number
  deficit?: number
  matched_pack?: number | null
}

export interface TeamDossier {
  available: boolean
  team_title?: string
  team_id?: number
  questions_total?: number
  took?: number
  expected?: number
  per_question_avg?: number
  tournaments?: TeamTournament[]
  calibration?: CalibrationBand[]
  focus_band?: [number, number]
  model?: { a: number; b: number; n: number } | null
  matched_questions?: number
  categories?: WeakTopicCategory[]
  techniques?: {
    technique: string
    questions: number
    took: number
    expected: number
    deficit: number
    per_question: number
  }[]
  players?: PlayerActivity[]
}

export interface PlayerActivity {
  id: number
  username: string
  display_name: string
  role: 'owner' | 'player'
  attempts: number
  questions: number
  correct: number
  success_pct: number | null
  last_attempt_at: string | null
  attempts_7d: number
}

export interface ForecastSwing {
  pos: number
  qid: number
  p: number
  tr: number
  category: string | null
  text: string
  answer: string
}

export interface PackForecast {
  pack_id: number
  pack_title: string
  questions: number
  expected: number
  field_avg: number
  focus_band: [number, number]
  bankers: number
  swing: ForecastSwing[]
}

/** Контур «Учить»: канон повторяющихся ответов и досье факта. */
export interface CanonItem {
  answer: string
  key: string
  count: number
  example_id: number
}

export interface StudyCanon {
  category_id: number | null
  category: string | null
  items: CanonItem[]
}

export interface FactAngle {
  id: number
  text: string
  comment: string
  pack_title: string | null
  year: string | null
  take_rate: number | null
}

export interface FactHook {
  fact: string
  angle: string
  grounded: boolean
}

export interface FactCard {
  core: string
  hooks: FactHook[]
}

export interface FactDossier {
  answer: string
  total: number
  angles: FactAngle[]
  card?: FactCard | null
}

/** Результат семантического поиска / похожих вопросов (по эмбеддингам). */
export interface SemanticHit {
  id: number
  similarity: number
  question_difficulty: number | null
  text_preview: string
  answer: string
  pack_title: string | null
  year: string | null
  category: string | null
}

export interface MapPoint {
  id: number
  x: number
  y: number
  c: string
  /** Приём: замена, пропуск, раздатка, блиц, цитата, чистый. */
  h: string
  t: string
  a: string
}

export interface MapResponse {
  available: boolean
  points: MapPoint[]
}

export interface SemanticResponse {
  available: boolean
  items: SemanticHit[]
}

export const api = {
  authSession: () => authRequest('/api/auth/session'),
  login: (username: string, password: string, remember: boolean) =>
    authRequest('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password, remember }),
    }),
  logout: async () => {
    await request<{ ok: boolean }>('/api/auth/logout', { method: 'POST' })
    csrfToken = ''
  },
  meta: () => request<Meta>('/api/meta'),
  overview: () => request<Overview>('/api/overview'),
  questions: (f: CatalogFilters) => request<CatalogPage>(`/api/questions${qs(f)}`),
  question: (id: number) => request<QuestionDetail>(`/api/questions/${id}`),
  topics: () => request<{ categories: TopicCategory[] }>('/api/topics'),
  weakTopics: () => request<WeakTopics>('/api/weak-topics'),
  teamDossier: () => request<TeamDossier>('/api/team/dossier'),
  teamForecast: (pack: number) =>
    request<PackForecast>(`/api/team/forecast${qs({ pack })}`),
  studyCanon: (category_id?: number | null, limit = 40) =>
    request<StudyCanon>(`/api/study/canon${qs({ category_id, limit })}`),
  studyFact: (answer: string) =>
    request<FactDossier>(`/api/study/fact${qs({ answer })}`),
  tournaments: (search = '', year?: number | null, limit = 60) =>
    request<TournamentPage>(
      `/api/tournaments${qs({ search, year, limit })}`,
    ),
  createPlayer: (body: {
    username: string
    display_name: string
    password: string
    telegram_id?: number | null
    vk_id?: number | null
  }) =>
    request<{
      id: number
      username: string
      display_name: string
      role: 'player'
      active: boolean
    }>('/api/admin/users', {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  similar: (id: number, top = 8) =>
    request<SemanticResponse>(`/api/questions/${id}/similar${qs({ top })}`),
  semanticSearch: (q: string, top = 20) =>
    request<SemanticResponse>(`/api/search/semantic${qs({ q, top })}`),
  semanticMap: () => request<MapResponse>('/api/semantic/map'),

  startTraining: (body: Record<string, unknown>) =>
    request<TrainingState>('/api/training/start', {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  trainingState: (id: string) => request<TrainingState>(`/api/training/${id}`),
  reveal: (id: string, user_answer: string) =>
    request<TrainingState>(`/api/training/${id}/reveal`, {
      method: 'POST',
      body: JSON.stringify({ user_answer }),
    }),
  grade: (id: string, knew: boolean) =>
    request<TrainingState>(`/api/training/${id}/grade`, {
      method: 'POST',
      body: JSON.stringify({ knew }),
    }),
  abort: (id: string) =>
    request<TrainingState>(`/api/training/${id}/abort`, { method: 'POST' }),
}
