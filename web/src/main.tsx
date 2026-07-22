import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

// Шрифты — локально, чтобы приложение работало офлайн.
// Кириллица обязательна: база вопросов на русском.
import '@fontsource/inter/cyrillic-400.css'
import '@fontsource/inter/cyrillic-500.css'
import '@fontsource/inter/cyrillic-600.css'
import '@fontsource/inter/latin-400.css'
import '@fontsource/inter/latin-500.css'
import '@fontsource/inter/latin-600.css'
// Вариативный пакет не разбит по субсетам: wght.css содержит и кириллицу, и латиницу.
import '@fontsource-variable/source-serif-4/wght.css'
import '@fontsource/jetbrains-mono/cyrillic-400.css'
import '@fontsource/jetbrains-mono/latin-400.css'

import './index.css'
import App from './App.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
