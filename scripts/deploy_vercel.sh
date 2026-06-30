#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

if ! command -v vercel >/dev/null 2>&1; then
  echo "Установите Vercel CLI: npm i -g vercel"
  exit 1
fi

if [[ -f .env ]]; then
  echo "Загружаем переменные из .env в Vercel (production)..."
  while IFS='=' read -r key value; do
    [[ -z "$key" || "$key" =~ ^# ]] && continue
    value="${value%$'\r'}"
    if [[ -n "$value" ]]; then
      printf '%s' "$value" | vercel env rm "$key" production --yes 2>/dev/null || true
      printf '%s' "$value" | vercel env add "$key" production
    fi
  done < .env
fi

vercel deploy --prod --yes

echo
echo "После деплоя:"
echo "1. Vercel Dashboard → Storage → Create → Upstash Redis (если ещё нет)"
echo "2. Read AI webhook URL: https://ВАШ-ПРОЕКТ.vercel.app/webhooks/readai"
echo "3. Напишите боту /start в Telegram"
