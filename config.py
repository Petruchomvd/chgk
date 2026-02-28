import os
from pathlib import Path

# Пути
PROJECT_ROOT = Path(__file__).parent

# Загрузка .env (без внешних зависимостей)
_env_path = PROJECT_ROOT / ".env"
if _env_path.exists():
    for line in _env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())
DB_PATH = PROJECT_ROOT / "chgk_analysis.db"
LEGACY_DB_PATH = PROJECT_ROOT / "chgk1.db"
OUTPUT_DIR = PROJECT_ROOT / "output"
CHARTS_DIR = OUTPUT_DIR / "charts"
REPORTS_DIR = OUTPUT_DIR / "reports"

# Парсинг
BASE_URL = "https://gotquestions.online"
PACK_URL = BASE_URL + "/pack/{pack_id}"
QUESTION_URL = BASE_URL + "/question/{question_id}"
INDEX_URL = BASE_URL + "/"

SCRAPE_DELAY = 0.5
SCRAPE_JITTER = 0.3
SCRAPE_BATCH_SIZE = 50
SCRAPE_BATCH_PAUSE = 10
SCRAPE_MAX_RETRIES = 3
SCRAPE_TIMEOUT = 15

# Классификация
OLLAMA_MODEL = "qwen2.5:7b-instruct-q4_K_M"
OLLAMA_FALLBACK_MODEL = "qwen2.5:3b-instruct"
CLASSIFICATION_BATCH_SIZE = 100
CLASSIFICATION_TEMPERATURE = 0.1
MIN_CONFIDENCE = 0.4

# Groq API (облачная классификация)
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL = "llama-3.3-70b-versatile"
GROQ_RATE_LIMIT_DELAY = 3  # секунды между запросами (free tier ~30 req/min)

# OpenAI API
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

# Anthropic API
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# Google Gemini API
GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")

# Telegram-уведомления (можно задать через .env или переменные окружения)
TELEGRAM_BOT_TOKEN = os.environ.get("CHGK_TG_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("CHGK_TG_CHAT_ID", "")
TELEGRAM_NOTIFY_INTERVAL = 30 * 60  # 30 минут (в секундах)
