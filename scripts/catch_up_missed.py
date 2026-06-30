#!/usr/bin/env python3
"""Догнать пропущенные уведомления о созвонах (с кнопкой, без брифа).

Бриф генерируется только после нажатия «Получить бриф» в Telegram.
Для разовой переотправки .html без кнопки: --redeliver-briefs
"""
from __future__ import annotations

import argparse
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
        "Явный запуск: ALLOW_LOCAL_SCRIPTS=1 python scripts/catch_up_missed.py"
    )

from app.config import settings
from app.kv import PendingMeetingStore, ProcessedMeetingsStore, SubscriberStore
from app.pipeline import catch_up_missed_deliveries

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--redeliver-briefs",
        action="store_true",
        help="Переотправить .html для уже обработанных созвонов (обходит кнопку)",
    )
    args = parser.parse_args()

    missing = settings.validate_runtime()
    if missing:
        raise SystemExit(f"Не хватает env: {', '.join(missing)}")

    subs = SubscriberStore(settings.database_path)
    pending = PendingMeetingStore(settings.database_path)
    processed = ProcessedMeetingsStore(settings.database_path)

    print(f"storage: {settings.storage_backend}")
    print(f"subscribers: {subs.count()} -> {subs.list_chat_ids()}")
    print(f"pending: {len(pending.list_ids())}")
    for meeting_id in pending.list_ids():
        print(
            f"  - {meeting_id} "
            f"(processed={processed.is_processed(meeting_id)})"
        )

    result = await catch_up_missed_deliveries(
        include_processed=args.redeliver_briefs
    )
    print("done:", result)


if __name__ == "__main__":
    asyncio.run(main())
