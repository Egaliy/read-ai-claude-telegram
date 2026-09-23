#!/usr/bin/env python3
"""Прогнать последнюю встречу (по части названия) через превью задач, как это делает вебхук.

  set -a; . ./.env; set +a
  TASKS_BOT_TOKEN=... TASK_PREVIEW_CHAT_IDS=... .venv/bin/python scripts/preview_tasks_by_person.py [часть названия]
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import task_preview  # noqa: E402
from app.models import ReadAIWebhookPayload  # noqa: E402


def latest_meeting(title_part: str) -> ReadAIWebhookPayload:
    import redis

    # Свой клиент: стенограммы тяжёлые, 5 с таймаута из kv.py мало.
    r = redis.from_url(os.environ["REDIS_URL"], decode_responses=True, socket_timeout=120)
    best = None
    for key in r.scan_iter("pending:meeting:*", count=500):
        raw = r.get(key)
        if not raw:
            continue
        p = ReadAIWebhookPayload.model_validate_json(raw)
        if title_part.lower() in (p.title or "").lower() and (best is None or (p.start_time or "") > (best.start_time or "")):
            best = p
    if not best:
        raise SystemExit(f"Встреч с «{title_part}» в названии не найдено")
    return best


if __name__ == "__main__":
    payload = latest_meeting(" ".join(sys.argv[1:]) or "daily")
    print(f"Встреча: {payload.title} · {payload.start_time}")
    print(task_preview.build_and_send(payload))
