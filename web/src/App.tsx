import { BrowserRouter, Route, Routes } from 'react-router-dom'
import {
  QueryClient,
  QueryClientProvider,
  onlineManager,
  useQuery,
} from '@tanstack/react-query'
import { useEffect } from 'react'
import { AppShell } from '@/components/AppShell'
import { AuthProvider } from '@/auth/AuthContext'
import { ApiError, api, type AuthSession } from '@/lib/api'
import { Overview } from '@/pages/Overview'
import { Catalog } from '@/pages/Catalog'
import { QuestionPage } from '@/pages/QuestionPage'
import { Topics } from '@/pages/Topics'
import { Training } from '@/pages/Training'
import { Session } from '@/pages/Session'
import { Review } from '@/pages/Review'
import { Search } from '@/pages/Search'
import { SemanticMap } from '@/pages/SemanticMap'
import { TeamDossier } from '@/pages/TeamDossier'
import { Study } from '@/pages/Study'
import { Login } from '@/pages/Login'

// Приложение ходит только на localhost, поэтому «офлайн» в понимании
// React Query здесь не применим: в этом состоянии запрос уходит в
// fetchStatus: 'paused', error остаётся null, а refetch() ничего не делает —
// и страница висит в загрузке с нерабочей кнопкой «Ещё раз».
// Считаем связь всегда доступной; недоступный бэкенд станет обычной ошибкой.
onlineManager.setOnline(true)

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
      // networkMode 'online' (по умолчанию) при недоступном бэкенде переводит
      // запрос в fetchStatus: 'paused' и НЕ выставляет error. Тогда страница
      // видит «данных нет» и показывает пустое состояние вместо ошибки —
      // то есть врёт, что база не классифицирована. Приложение ходит на
      // localhost, эвристика онлайна тут вредна: всегда выполняем запрос.
      networkMode: 'always',
    },
    mutations: { networkMode: 'always' },
  },
})

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AuthGate />
    </QueryClientProvider>
  )
}

function AuthGate() {
  const { data, error, isLoading, refetch } = useQuery<AuthSession | null>({
    queryKey: ['auth-session'],
    queryFn: api.authSession,
    retry: false,
    staleTime: 5 * 60_000,
  })

  useEffect(() => {
    const expire = () => queryClient.setQueryData(['auth-session'], null)
    window.addEventListener('chgk:session-expired', expire)
    return () => window.removeEventListener('chgk:session-expired', expire)
  }, [])

  function acceptSession(session: AuthSession) {
    queryClient.setQueryData(['auth-session'], session)
  }

  async function logout() {
    try {
      await api.logout()
    } finally {
      queryClient.clear()
      queryClient.setQueryData(['auth-session'], null)
    }
  }

  if (isLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-paper">
        <div className="text-center">
          <div className="login-loader mx-auto" aria-hidden />
          <p className="mt-4 text-xs text-muted-foreground">Открываем картотеку…</p>
        </div>
      </div>
    )
  }

  if (!data) {
    if (error && (!(error instanceof ApiError) || error.status !== 401)) {
      return (
        <div className="flex min-h-screen items-center justify-center bg-paper px-5">
          <div className="max-w-sm text-center">
            <h1 className="font-serif text-2xl font-semibold">Сервер не отвечает</h1>
            <p className="mt-3 text-sm leading-6 text-muted-foreground">
              Не удалось открыть страницу входа. Проверьте соединение и попробуйте ещё раз.
            </p>
            <button className="login-submit mt-6" onClick={() => refetch()}>
              Попробовать снова
            </button>
          </div>
        </div>
      )
    }
    return <Login onSuccess={acceptSession} />
  }

  return (
    <AuthProvider user={data.user} logout={logout}>
      <BrowserRouter>
        <AppShell>
          <Routes>
            <Route path="/" element={<Overview />} />
            <Route path="/catalog" element={<Catalog />} />
            <Route path="/question/:id" element={<QuestionPage />} />
            <Route path="/topics" element={<Topics />} />
            <Route path="/training" element={<Training />} />
            <Route path="/training/:sessionId" element={<Session />} />
            <Route path="/review" element={<Review />} />
            <Route path="/search" element={<Search />} />
            <Route path="/map" element={<SemanticMap />} />
            <Route path="/team" element={<TeamDossier />} />
            <Route path="/study" element={<Study />} />
          </Routes>
        </AppShell>
      </BrowserRouter>
    </AuthProvider>
  )
}
