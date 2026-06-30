# Read AI → Claude → Telegram

Автоматически после звонка Read AI шлёт webhook с транскрипцией → Claude извлекает задачи по вашему промпту → результат уходит в Telegram.

## Что нужно от вас

1. Скопировать `.env.example` в `.env` и заполнить ключи
2. При необходимости отредактировать промпт в `prompts/tasks_extraction.txt`
3. Поднять сервер и указать URL webhook в Read AI

## Быстрый старт

```bash
cp .env.example .env
# отредактируйте .env

chmod +x scripts/run.sh
./scripts/run.sh
```

Сервер: `http://localhost:8000`  
Webhook endpoint: `POST /webhooks/readai`  
Health check: `GET /health`

## Переменные окружения

| Переменная | Обязательно | Описание |
|------------|-------------|----------|
| `ANTHROPIC_API_KEY` | да | Ключ Claude: [console.anthropic.com](https://console.anthropic.com/settings/keys) |
| `TELEGRAM_BOT_TOKEN` | да | Токен от [@BotFather](https://t.me/BotFather) |
| `TELEGRAM_CHAT_ID` | нет | Доп. chat_id (основная рассылка — всем, кто написал `/start`) |
| `READAI_WEBHOOK_SIGNING_KEY` | рекомендуется | Signing key из настроек webhook Read AI |
| `CLAUDE_MODEL` | нет | Модель Claude (по умолчанию `claude-sonnet-4-20250514`) |
| `PROMPT_FILE` | нет | Путь к system-промпту |
| `MEETING_TITLE_FILTER` | нет | Обрабатывать только встречи, в названии которых есть эта строка |
| `PORT` | нет | Порт сервера (8000) |

### Telegram-рассылка

1. Запустите сервер: `./scripts/run.sh`
2. Найдите бота в Telegram и отправьте **`/start`**
3. Все, кто написали `/start`, будут получать задачи после звонков
4. Отписка: **`/stop`**

Проверить число подписчиков: `GET /health` → `telegram_subscribers`

**Production:** задайте `TELEGRAM_WEBHOOK_URL=https://ВАШ-ДОМЕН/webhooks/telegram` и `TELEGRAM_POLLING=false`

## Настройка Read AI

1. Нужен план **Pro / Enterprise** (webhooks — Premium)
2. Откройте [Integrations → Webhooks](https://app.read.ai/analytics/integrations/webhooks)
3. URL webhook: `https://ВАШ-ДОМЕН/webhooks/readai`
4. Сохраните **signing key** в `READAI_WEBHOOK_SIGNING_KEY`

### Локальная отладка через ngrok

```bash
ngrok http 8000
```

В Read AI укажите: `https://xxxx.ngrok-free.app/webhooks/readai`

## Деплой на Vercel

```bash
npx vercel deploy --prod --yes
```

Production URL: **https://read-ai-claude-telegram.vercel.app**

**Read AI → HTTPS URL:**
```
https://read-ai-claude-telegram.vercel.app/webhooks/readai
```

**Рекомендуется:** Vercel Dashboard → Storage → Upstash Redis — чтобы подписчики `/start` сохранялись между запусками.

## Локальный тест без Read AI

```bash
curl -X POST http://localhost:8000/test/process \
  -H "Content-Type: application/json" \
  -d @samples/webhook_sample.json
```

## Структура проекта

```
app/
  main.py           # FastAPI, webhook endpoint
  pipeline.py       # оркестрация: Claude → Telegram
  config.py         # настройки из .env
  security.py       # проверка подписи Read AI
  services/
    claude.py       # вызов Anthropic API
    telegram.py     # отправка в Telegram
    transcript.py   # извлечение текста из webhook
prompts/
  tasks_extraction.txt   # ваш system-промпт
samples/
  webhook_sample.json    # пример payload для теста
```

## Деплой

Любой хостинг с HTTPS и Python 3.11+:

- Railway, Render, Fly.io, VPS

Пример запуска в production:

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

## Поведение

- Webhook сразу отвечает `200 accepted`, обработка идёт в фоне
- Одна встреча обрабатывается один раз (SQLite в `data/processed.db`)
- Длинные ответы автоматически режутся на несколько сообщений Telegram
- Если задан `MEETING_TITLE_FILTER`, обрабатываются только подходящие по названию встречи
