import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider, onlineManager } from '@tanstack/react-query'
import { AppShell } from '@/components/AppShell'
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
    </QueryClientProvider>
  )
}
