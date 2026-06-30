#!/usr/bin/env python3
"""Повторно отправить уведомление о последнем созвоне для теста кнопок."""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

if __import__("os").getenv("ALLOW_LOCAL_SCRIPTS") != "1":
    raise SystemExit(
        "Скрипт отключён. Рассылка только через Vercel.\n"
        "Явный запуск: ALLOW_LOCAL_SCRIPTS=1 python scripts/resend_last_meeting_test.py"
    )

from app.config import settings
from app.pipeline import (
    find_longest_pending_meeting,
    reset_meeting_for_retest,
    resend_meeting_notification,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


async def main() -> None:
    missing = settings.validate_runtime()
    if missing:
        raise SystemExit(f"Не хватает env: {', '.join(missing)}")

    payload = await find_longest_pending_meeting()
    if not payload:
        raise SystemExit("В Redis нет сохранённых созвонов")

    meeting_id = payload.meeting_key
    title = payload.title or meeting_id
    print(f"Самый длинный созвон: {title} ({meeting_id})")
    print()
    print("⚠️  Скрипт отправит уведомление ВСЕМ подписчикам в Telegram.")
    print("    Запускайте только по явной просьбе — не для фоновых тестов.")
    confirm = input("Отправить? [y/N]: ").strip().lower()
    if confirm not in {"y", "yes", "д", "да"}:
        raise SystemExit("Отменено.")

    await reset_meeting_for_retest(meeting_id)
    await resend_meeting_notification(payload)
    print("Уведомление с кнопкой «Получить бриф» отправлено.")
    print("Дальше в Telegram: бриф → «Добавить в Notion».")


if __name__ == "__main__":
    asyncio.run(main())
