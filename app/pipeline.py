from __future__ import annotations

import json
import logging
from typing import Optional

from app.config import settings
from app.kv import BriefStore, BriefHtmlStore, NotifiedMeetingsStore, PendingMeetingStore
from app.models import ReadAIWebhookPayload
from app.security import ProcessedMeetingsStore
from app.services.brief_html import markdown_brief_to_html
from app.services.claude import extract_meeting_brief
from app.services.telegram import deliver_meeting_brief, send_meeting_pending_notifications

logger = logging.getLogger(__name__)
processed_store = ProcessedMeetingsStore(settings.database_path)
pending_store = PendingMeetingStore(settings.database_path)
notified_store = NotifiedMeetingsStore(settings.database_path)
brief_store = BriefStore(settings.database_path)
brief_html_store = BriefHtmlStore(settings.database_path)


def _passes_title_filter(payload: ReadAIWebhookPayload) -> bool:
    if not settings.meeting_title_filter:
        return True
    title = payload.title or ""
    return settings.meeting_title_filter.lower() in title.lower()


def _serialize_payload(payload: ReadAIWebhookPayload) -> str:
    return json.dumps(payload.model_dump(mode="json"), ensure_ascii=False)


def load_pending_payload(meeting_id: str) -> ReadAIWebhookPayload:
    raw = pending_store.load(meeting_id)
    if raw:
        return ReadAIWebhookPayload.model_validate_json(raw)

    for candidate_id in pending_store.list_ids():
        candidate_raw = pending_store.load(candidate_id)
        if not candidate_raw:
            continue
        payload = ReadAIWebhookPayload.model_validate_json(candidate_raw)
        if meeting_id in payload.storage_keys:
            return payload

    raise ValueError("Данные созвона не найдены или истекли")


def resolve_pending_meeting_id(meeting_id: str) -> Optional[str]:
    if pending_store.load(meeting_id):
        return meeting_id

    for candidate_id in pending_store.list_ids():
        candidate_raw = pending_store.load(candidate_id)
        if not candidate_raw:
            continue
        payload = ReadAIWebhookPayload.model_validate_json(candidate_raw)
        if meeting_id in payload.storage_keys:
            return payload.meeting_key

    return None


def iter_unique_pending_payloads():
    seen: set[str] = set()
    for meeting_id in pending_store.list_ids():
        raw = pending_store.load(meeting_id)
        if not raw:
            continue
        payload = ReadAIWebhookPayload.model_validate_json(raw)
        key = payload.meeting_key
        if key in seen:
            continue
        seen.add(key)
        yield key, payload


async def queue_meeting(payload: ReadAIWebhookPayload) -> None:
    meeting_id = payload.meeting_key

    if processed_store.is_processed(meeting_id):
        logger.info("Встреча %s уже обработана, пропуск", meeting_id)
        return

    if not _passes_title_filter(payload):
        logger.info(
            "Встреча %s не прошла фильтр по названию (%r), пропуск",
            meeting_id,
            settings.meeting_title_filter,
        )
        return

    pending_store.save(meeting_id, _serialize_payload(payload))
    for alias in payload.storage_keys:
        if alias != meeting_id:
            pending_store.save(alias, _serialize_payload(payload))
    logger.info("Созвон %s сохранён в очередь", meeting_id)

    # Старое уведомление «Прошёл созвон» с кнопками отключено: вместо него приходит
    # сообщение с задачами и два документа (app/task_preview.py).
    from app import task_preview

    if task_preview.enabled():
        logger.info("Старое уведомление по %s пропущено — работает новый формат", meeting_id)
        return

    if notified_store.is_notified(meeting_id):
        logger.info("Уведомление для %s уже отправлялось", meeting_id)
        return

    await send_meeting_pending_notifications(payload)
    notified_store.mark_notified(meeting_id)
    logger.info("Уведомление о созвоне %s отправлено в Telegram", meeting_id)


async def process_meeting(
    payload: ReadAIWebhookPayload,
    *,
    skip_intro: bool = False,
) -> None:
    meeting_id = payload.meeting_key
    title = payload.title or ""

    if processed_store.is_processed(meeting_id):
        logger.info("Встреча %s уже обработана, пропуск", meeting_id)
        return

    if not _passes_title_filter(payload):
        logger.info(
            "Встреча %s не прошла фильтр по названию (%r), пропуск",
            meeting_id,
            settings.meeting_title_filter,
        )
        return

    logger.info("Обработка встречи %s: %s", meeting_id, title or "без названия")

    brief = extract_meeting_brief(payload)
    brief_html = markdown_brief_to_html(brief)
    brief_store.save(meeting_id, brief)
    brief_html_store.save(meeting_id, brief_html)
    await deliver_meeting_brief(payload, brief, skip_intro=skip_intro)

    processed_store.mark_processed(meeting_id)
    logger.info("Встреча %s успешно отправлена в Telegram", meeting_id)


async def process_meeting_by_id(meeting_id: str) -> None:
    payload = load_pending_payload(meeting_id)
    await process_meeting(payload, skip_intro=True)


async def find_longest_pending_meeting() -> Optional[ReadAIWebhookPayload]:
    from datetime import datetime

    def parse_dt(value: Optional[str]):
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None

    longest_payload: Optional[ReadAIWebhookPayload] = None
    longest_seconds = -1.0

    for meeting_id, payload in iter_unique_pending_payloads():
        if meeting_id.startswith("test-"):
            continue
        start = parse_dt(payload.start_time)
        end = parse_dt(payload.end_time)
        if not start or not end:
            continue
        duration = (end - start).total_seconds()
        if duration > longest_seconds:
            longest_seconds = duration
            longest_payload = payload

    return longest_payload


async def find_latest_pending_meeting() -> Optional[ReadAIWebhookPayload]:
    return await find_longest_pending_meeting()


async def reset_meeting_for_retest(meeting_id: str) -> None:
    """Сбросить статус созвона для повторного теста кнопок."""
    processed_store.unmark_processed(meeting_id)
    brief_store.delete(meeting_id)
    brief_html_store.delete(meeting_id)
    from app.kv import MeetingProjectStore, NotionPageStore

    NotionPageStore(settings.database_path).delete(meeting_id)
    MeetingProjectStore(settings.database_path).delete(meeting_id)


async def resend_meeting_notification(payload: ReadAIWebhookPayload) -> None:
    """Повторно отправить уведомление с кнопкой (без генерации брифа)."""
    await send_meeting_pending_notifications(payload)


async def catch_up_pending_notifications() -> int:
    """Отключено: массовая рассылка истории не используется."""
    logger.warning("catch_up_pending_notifications отключён — пропуск")
    return 0


async def redeliver_meeting_brief(meeting_id: str) -> None:
    """Повторно отправить бриф всем подписчикам (без повторной пометки processed)."""
    payload = load_pending_payload(meeting_id)
    brief = extract_meeting_brief(payload)
    brief_html = markdown_brief_to_html(brief)
    brief_store.save(meeting_id, brief)
    brief_html_store.save(meeting_id, brief_html)
    await deliver_meeting_brief(payload, brief, skip_intro=True)
    logger.info("Catch-up: бриф по созвону %s переотправлен", meeting_id)


async def catch_up_missed_deliveries(*, include_processed: bool = False) -> dict:
    """Отключено: массовая рассылка истории не используется."""
    logger.warning("catch_up_missed_deliveries отключён — пропуск")
    return {
        "notifications_sent": 0,
        "briefs_redelivered": 0,
        "errors": [],
    }
