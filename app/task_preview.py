"""Задачи по людям после каждой встречи → превью в отдельного бота (TASKS_BOT_TOKEN).

Пока получатели — только TASK_PREVIEW_CHAT_IDS (руководство): видно, что получит каждый исполнитель.
Бриф строится той же нейронкой и промптом, что и основной бриф; затем второй запрос делит задачи по людям.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from typing import List

import anthropic
import httpx

from app.config import settings
from app.models import ReadAIWebhookPayload
from app.services.claude import extract_meeting_brief
from app.services.transcript import build_claude_source_text

logger = logging.getLogger(__name__)

SENT_PREFIX = "taskpreview:sent:"
SENT_TTL_SECONDS = 60 * 60 * 24 * 30

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["people"],
    "properties": {
        "people": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "tasks"],
                "properties": {
                    "name": {"type": "string"},
                    "tasks": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["title", "emoji", "due"],
                            "properties": {
                                "title": {"type": "string"},
                                "emoji": {"type": "string"},
                                "due": {"type": "string"},
                            },
                        },
                    },
                },
            },
        }
    },
}


def enabled() -> bool:
    return bool(os.getenv("TASKS_BOT_TOKEN") and chat_ids())


def chat_ids() -> List[str]:
    raw = os.getenv("TASK_PREVIEW_CHAT_IDS", "")
    return [c for c in raw.replace(",", " ").split() if c.lstrip("-").isdigit()]


def internal_token() -> str:
    """Токен для внутреннего вызова /internal/task-preview — выводится из ключа вебхука."""
    key = (settings.readai_webhook_signing_key or "").encode()
    return hmac.new(key, b"task-preview", hashlib.sha256).hexdigest()


def _redis():
    import redis

    url = os.getenv("REDIS_URL")
    return redis.from_url(url, decode_responses=True, socket_timeout=120) if url else None


def claim(meeting_id: str) -> bool:
    """True, если превью по встрече ещё не отправлялось (и помечает его отправленным)."""
    r = _redis()
    if r is None:
        return True
    return bool(r.set(SENT_PREFIX + meeting_id, "1", nx=True, ex=SENT_TTL_SECONDS))


def release(meeting_id: str) -> None:
    r = _redis()
    if r is not None:
        r.delete(SENT_PREFIX + meeting_id)


def split_by_person(brief: str, transcript: str) -> list:
    system = (settings.prompt_path.parent / "tasks_by_person.txt").read_text(encoding="utf-8")
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    msg = client.messages.create(
        model=settings.claude_model,
        max_tokens=8192,
        system=system,
        output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": f"BRIEF:\n{brief}\n\nTRANSCRIPT:\n{transcript}"}],
    )
    text = "".join(b.text for b in msg.content if b.type == "text")
    return json.loads(text)["people"]


def render(title: str, date: str, tasks: list) -> str:
    lines = [f"🗂 Ваши задачи · {title}", f"📅 {date}", ""]
    for t in tasks:
        due = t.get("due", "").strip()
        lines.append(f"{t.get('emoji') or '▫️'} {t['title']}" + (f"  ⏰ {due}" if due else ""))
    return "\n".join(lines)


def _send(text: str) -> None:
    token = os.getenv("TASKS_BOT_TOKEN")
    for chat in chat_ids():
        try:
            httpx.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat, "text": text[:4000]},
                timeout=30,
            ).raise_for_status()
        except Exception:
            logger.exception("Превью задач: не отправилось в %s", chat)


def build_and_send(payload: ReadAIWebhookPayload) -> int:
    title = payload.title or "встреча"
    date = (payload.start_time or "")[:10]
    brief = extract_meeting_brief(payload)
    transcript, _ = build_claude_source_text(payload)
    people = [p for p in split_by_person(brief, transcript) if p.get("tasks")]

    if not people:
        _send(f"🗂 {title} · {date}\nЗадач на встрече не найдено.")
        return 0
    _send(f"🧪 {title} · {date}\nЗадачи по людям — так их увидит каждый. Исполнителей: {len(people)}.")
    for person in people:
        _send(f"👤 {person['name']}\n\n" + render(title, date, person["tasks"]))
    return len(people)


def trigger(meeting_id: str) -> None:
    """Запустить превью отдельным вызовом, чтобы не держать вебхук Read.ai."""
    base = f"https://{settings.vercel_stable_domain}"
    try:
        httpx.post(
            f"{base}/internal/task-preview",
            json={"meeting_id": meeting_id},
            headers={"x-internal-token": internal_token()},
            timeout=3,
        )
    except httpx.TimeoutException:
        pass  # ожидаемо: вызов продолжает работать сам
    except Exception:
        logger.exception("Не удалось запустить превью задач для %s", meeting_id)
