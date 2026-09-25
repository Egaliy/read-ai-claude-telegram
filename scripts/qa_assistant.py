#!/usr/bin/env python3
"""Прогон вопросов к ассистенту. Ничего не отправляет в Telegram.

  set -a; . ./.env; set +a; .venv/bin/python scripts/qa_assistant.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import assistant  # noqa: E402

QUESTIONS = sys.argv[1:] or [
    "мне что-то прилетало по AskYogi? что от меня ждут",
    "экран выбора языка ещё актуален или отменили?",
    "клиент что-то говорил про фон welcome-экрана, его вообще можно менять?",
    "когда дедлайн по мобильной адаптации сайта студии?",
    "что нового по сайту студии за последние дни?",
]

for q in QUESTIONS:
    t0 = time.time()
    print("╔═", q)
    asker = ""
    if q.startswith("["):  # «[Имя] вопрос» — имитируем, что вопрос пришёл от человека
        asker, q = q[1:].split("]", 1)
        q = q.strip()
    print(assistant.answer(q, asker=asker))
    print(f"╚═ {time.time()-t0:.0f} с\n")
