# Repository Structure

This file is the canonical map of the project after cleanup work.

## Active product path

- `bot/`: Telegram training bot.
- `vk_bot/`: VK training bot.
- `app/`: training and study business logic.
- `database/`: SQLite access and schemas.
- `scraper/`: current HTTP-based parsers and runners.
- `dashboard/`: Streamlit analytics and training views.
- `classifier/`: classification pipeline and provider adapters.
- `scripts/`: maintained operational and research entrypoints.
- `tests/`: the only pytest-discovered test suite.

## Local and generated artifacts

These are runtime outputs or personal research materials and should not be
treated as source code:

- `chgk_analysis.db` — локальная копия до переноса на сервер или проверенная резервная копия
- `training.db`
- `studies/`
- `output/`
- `coverage.xml`, `.coverage`, `htmlcov/`
- root-level `data/*.md`, `data/*.txt`, `data/*.pdf`, `data/*.docx`
- cache-style files under `data/gentleman_set/`
- generated YouTube transcript files under `data/youtube/transcripts/`

В рабочем серверном окружении обе базы хранятся вне Git-клона. Пути задаются
через `CHGK_DB_PATH` и `CHGK_TRAINING_DB_PATH`; GitHub не используется для их
доставки.

## Legacy and archival areas

These files are archived and are not the main product path:

- `archive/legacy_parser/`: old Selenium-based parser stack
- `archive/ad_hoc/`: root-level one-off scripts like `test.py`, `test_questions.py`, `testdb.py`
- `archive/analytics_experiments/`: one-off analytics helpers
- `chgk.db`, `chgk1.db`

The current scraper stack no longer depends on `modules_pars/`, and benchmark ids now live in `scripts/benchmark_ids.py`.

## Telegram env vars

- `CHGK_BOT_TOKEN`: training bot token.
- `CHGK_BOT_OWNER_TG_ID`: legacy owner id and migration anchor.
- `CHGK_BOT_ALLOWED_TG_IDS`: allowlist for team usage in private chat.
- `CHGK_TG_BOT_TOKEN` / `CHGK_TG_CHAT_ID`: notifier flow.
- `TG_DIGEST_BOT_TOKEN` / `TG_DIGEST_CHAT_ID`: digest/posting scripts.

## VK env vars

- `CHGK_VK_BOT_TOKEN`: VK community access token for the training bot.
- `CHGK_VK_GROUP_ID`: VK community id used by long polling.
- `CHGK_VK_OWNER_USER_ID`: owner VK user id.
- `CHGK_VK_ALLOWED_USER_IDS`: allowlist for private VK chats.
- `CHGK_VK_REMINDER_HOUR`: optional daily reminder hour.
