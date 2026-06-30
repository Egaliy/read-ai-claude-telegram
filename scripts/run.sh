#!/usr/bin/env bash
set -euo pipefail

if [[ "${ALLOW_LOCAL:-}" != "1" ]]; then
  echo "Локальный сервер отключён — бот работает только на Vercel."
  echo "Production: https://read-ai-claude-telegram.vercel.app"
  echo ""
  echo "Деплой: npx vercel deploy --prod --yes"
  exit 1
fi

cd "$(dirname "$0")/.."

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi

source .venv/bin/activate
pip install -q -r requirements.txt

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Создан .env — заполните ключи и перезапустите сервер."
fi

exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --reload
