#!/usr/bin/env bash
# Запуск веб-приложения «Картотека»: FastAPI + Vite.
#
#   ./scripts/run_web.sh
#
# Откроется http://localhost:5173 (фронтенд проксирует /api на :8000).
# Streamlit-дашборд с аналитикой это не затрагивает — он запускается как раньше:
#   streamlit run dashboard/app.py

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

API_PORT=8000
WEB_PORT=5173

if [ ! -x .venv/bin/python ] || ! .venv/bin/python -c "import uvicorn" >/dev/null 2>&1; then
  echo "Нет рабочего uvicorn в .venv. Установите зависимости:" >&2
  echo "  .venv/bin/python -m pip install -r requirements-web.txt" >&2
  exit 1
fi

if [ ! -d web/node_modules ]; then
  echo "Нет web/node_modules. Установите зависимости:" >&2
  echo "  cd web && npm install" >&2
  exit 1
fi

# Порты проверяем заранее и по имени.
# Иначе Vite при занятом 5173 молча уезжает на 5174, а CORS в api/main.py
# разрешает только 5173 — приложение откроется, но все запросы к API упадут,
# и по экрану причину не понять.
port_busy() {
  lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1
}

for port_pair in "$API_PORT API" "$WEB_PORT веб"; do
  set -- $port_pair
  if port_busy "$1"; then
    echo "Порт $1 ($2) занят. Кто именно:" >&2
    lsof -nP -iTCP:"$1" -sTCP:LISTEN | tail -n +2 >&2
    echo >&2
    echo "Освободите порт или остановите старый экземпляр:" >&2
    echo "  kill \$(lsof -t -iTCP:$1 -sTCP:LISTEN)" >&2
    exit 1
  fi
done

cleanup() {
  # Гасим обе половины, чтобы не оставлять сирот при Ctrl-C.
  [ -n "${API_PID:-}" ] && kill "$API_PID" 2>/dev/null || true
  [ -n "${WEB_PID:-}" ] && kill "$WEB_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "→ API   http://127.0.0.1:$API_PORT"
# --reload: правки api/ и app/ подхватываются без перезапуска — иначе сервер
# молча живёт со старым кодом и новые режимы отвечают «Неизвестный режим».
.venv/bin/python -m uvicorn api.main:app --host 127.0.0.1 --port "$API_PORT" --reload &
API_PID=$!

# --strictPort: упасть с ошибкой, а не переехать на другой порт втихую.
echo "→ Веб   http://localhost:$WEB_PORT"
(cd web && npm run dev -- --port "$WEB_PORT" --strictPort) &
WEB_PID=$!

wait
