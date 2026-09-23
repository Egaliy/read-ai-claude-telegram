"""Дайджест звонка для всей команды: проект, один абзац саммари, задачи, кнопка «Полный транскрипт».

Уходит в отдельного бота (TASKS_BOT_TOKEN) всем получателям из TASK_PREVIEW_CHAT_IDS.
Дейлики пропускаются (DIGEST_SKIP_TITLES). Финансы и юридическое вырезаются и из дайджеста,
и из транскрипта, который отдаётся по кнопке.
"""
from __future__ import annotations

import hashlib
import hmac
import io
import json
import logging
import os
import re
from typing import List, Optional

import anthropic
import httpx

from app.config import settings
from app.models import ReadAIWebhookPayload
from app.services.transcript import build_claude_source_text

logger = logging.getLogger(__name__)

SENT_PREFIX = "digest:sent:"
CLEAN_PREFIX = "digest:transcript:"
CHATS_KEY = "digest:chats"
TTL_SECONDS = 60 * 60 * 24 * 60
DEFAULT_SKIP = r"daily|дейл|дэйл|standup|стендап|планёрка|планерка"

DIGEST_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["project", "summary", "tasks"],
    "properties": {
        "project": {"type": "string"},
        "summary": {"type": "string"},
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
}


# ---------- настройки ----------
def enabled() -> bool:
    return bool(os.getenv("TASKS_BOT_TOKEN") and chat_ids())


def chat_ids() -> List[str]:
    """Получатели: из env плюс чаты, куда бота добавили (он запоминает их сам)."""
    raw = os.getenv("TASK_PREVIEW_CHAT_IDS", "")
    ids = [c for c in raw.replace(",", " ").split() if c.lstrip("-").isdigit()]
    r = _redis()
    if r is not None:
        try:
            ids += [c for c in r.smembers(CHATS_KEY) if c not in ids]
        except Exception:
            logger.exception("Не прочитались чаты-получатели")
    return ids


def remember_chat(update: dict) -> None:
    """Бота добавили в чат или удалили из него — обновляем список получателей."""
    member = update.get("my_chat_member") or {}
    chat = member.get("chat") or {}
    status = (member.get("new_chat_member") or {}).get("status")
    if not chat.get("id") or not status:
        return
    r = _redis()
    if r is None:
        return
    if status in {"member", "administrator", "creator"}:
        r.sadd(CHATS_KEY, str(chat["id"]))
        _send(str(chat["id"]), "Готово. Сюда будут приходить итоги всех звонков: проект, краткое саммари и задачи. Дейлики не присылаю. Финансы и юридическое вырезаны.")
        logger.info("Чат %s (%s) добавлен в получатели", chat["id"], chat.get("title"))
    else:
        r.srem(CHATS_KEY, str(chat["id"]))
        logger.info("Чат %s убран из получателей", chat["id"])


def is_skipped(title: str) -> bool:
    """Дейлики и планёрки в общий бот не идут."""
    return bool(re.search(os.getenv("DIGEST_SKIP_TITLES", DEFAULT_SKIP), title or "", re.I))


def internal_token() -> str:
    key = (settings.readai_webhook_signing_key or "").encode()
    return hmac.new(key, b"task-preview", hashlib.sha256).hexdigest()


def webhook_secret() -> str:
    token = (os.getenv("TASKS_BOT_TOKEN") or "").encode()
    return hmac.new(token, b"tasks-bot-webhook", hashlib.sha256).hexdigest()[:48]


def _redis():
    import redis

    url = os.getenv("REDIS_URL")
    return redis.from_url(url, decode_responses=True, socket_timeout=120) if url else None


def claim(meeting_id: str) -> bool:
    r = _redis()
    if r is None:
        return True
    return bool(r.set(SENT_PREFIX + meeting_id, "1", nx=True, ex=TTL_SECONDS))


def release(meeting_id: str) -> None:
    r = _redis()
    if r is not None:
        r.delete(SENT_PREFIX + meeting_id)


# ---------- Claude ----------
def _client() -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=settings.anthropic_api_key)


def _prompt(name: str) -> str:
    return (settings.prompt_path.parent / name).read_text(encoding="utf-8")


def build_digest(title: str, transcript: str) -> dict:
    msg = _client().messages.create(
        model=settings.claude_model,
        max_tokens=8192,
        system=_prompt("meeting_digest.txt"),
        output_config={"format": {"type": "json_schema", "schema": DIGEST_SCHEMA}},
        messages=[{"role": "user", "content": f"MEETING TITLE: {title}\n\nTRANSCRIPT:\n{transcript}"}],
    )
    return json.loads("".join(b.text for b in msg.content if b.type == "text"))


def clean_transcript(transcript: str) -> str:
    # Стриминг обязателен: ответ длинный, обычный запрос SDK отклоняет.
    with _client().messages.stream(
        model=settings.claude_model,
        max_tokens=32000,
        system=_prompt("transcript_redaction.txt"),
        messages=[{"role": "user", "content": transcript}],
    ) as stream:
        msg = stream.get_final_message()
    return "".join(b.text for b in msg.content if b.type == "text").strip()


def cached_clean_transcript(meeting_id: str, transcript: str) -> str:
    r = _redis()
    if r is not None:
        cached = r.get(CLEAN_PREFIX + meeting_id)
        if cached:
            return cached
    cleaned = clean_transcript(transcript)
    if r is not None:
        r.set(CLEAN_PREFIX + meeting_id, cleaned, ex=TTL_SECONDS)
    return cleaned


# ---------- Telegram ----------
def _api(method: str, payload: dict, files=None):
    token = os.getenv("TASKS_BOT_TOKEN")
    url = f"https://api.telegram.org/bot{token}/{method}"
    if files:
        return httpx.post(url, data=payload, files=files, timeout=60)
    return httpx.post(url, json=payload, timeout=30)


def _send(chat: str, text: str, markup: Optional[dict] = None) -> None:
    body = {"chat_id": chat, "text": text[:4000], "link_preview_options": {"is_disabled": True}}
    if markup:
        body["reply_markup"] = markup
    res = _api("sendMessage", body)
    if res.status_code != 200:
        logger.error("Дайджест не отправился в %s: %s", chat, res.text[:200])


def format_digest(payload: ReadAIWebhookPayload, digest: dict) -> str:
    date = (payload.start_time or "")[:10]
    lines = [f"📞 {digest['project']} · {date}", "", digest["summary"].strip()]
    if digest.get("tasks"):
        lines += ["", "Задачи:"]
        for t in digest["tasks"]:
            due = (t.get("due") or "").strip()
            # Модель иногда ставит эмодзи и в начало самой задачи — убираем дубль.
            title = re.sub(r"^[^\w\d(«\"']+", "", t["title"]).strip()
            lines.append(f"{t.get('emoji') or '▫️'} {title}" + (f"  ⏰ {due}" if due else ""))
    return "\n".join(lines)


def _file_name(payload: ReadAIWebhookPayload, prefix: str, ext: str) -> str:
    base = re.sub(r"[^\w\-. ]+", "", payload.title or "meeting").strip().replace(" ", "-")[:50] or "meeting"
    return f"{prefix}-{base}-{(payload.start_time or '')[:10]}.{ext}"


def _send_document(chat: str, name: str, content: str, mime: str, caption: str = "") -> None:
    res = _api(
        "sendDocument",
        {"chat_id": chat, "caption": caption[:900]},
        files={"document": (name, io.BytesIO(content.encode("utf-8")), mime)},
    )
    if res.status_code != 200:
        logger.error("Файл %s не отправился в %s: %s", name, chat, res.text[:200])


def send_transcript(payload: ReadAIWebhookPayload) -> dict:
    """Шаг 2: очищенный транскрипт файлом."""
    transcript, _ = build_claude_source_text(payload)
    cleaned = cached_clean_transcript(payload.meeting_key, transcript)
    caption = f"📄 Транскрипт: {payload.title or 'встреча'}\nФинансы и юридическое вырезаны."
    for chat in chat_ids():
        _send_document(chat, _file_name(payload, "transcript", "txt"), cleaned, "text/plain", caption)
    return {"transcript": len(cleaned)}


def build_and_send(payload: ReadAIWebhookPayload) -> dict:
    """Шаг 1: саммари с задачами и бриф .html. Транскрипт уходит следующим вызовом."""
    title = payload.title or "встреча"
    if is_skipped(title):
        logger.info("Дайджест: %s пропущен (дейлик)", title)
        return {"skipped": True}

    from app.services.brief_html import markdown_brief_to_html
    from app.services.claude import extract_meeting_brief

    transcript, _ = build_claude_source_text(payload)
    digest = build_digest(title, transcript)
    chats = chat_ids()
    for chat in chats:
        _send(chat, format_digest(payload, digest))

    brief_ok = False
    try:
        brief_html = markdown_brief_to_html(extract_meeting_brief(payload))
        for chat in chats:
            _send_document(chat, _file_name(payload, "brief", "html"), brief_html, "text/html", f"📋 Бриф: {title}")
        brief_ok = True
    except Exception:
        logger.exception("Бриф для %s не собрался", payload.meeting_key)

    trigger(payload.meeting_key, stage="transcript")
    return {"project": digest["project"], "tasks": len(digest.get("tasks", [])), "brief": brief_ok, "chats": len(chats)}


def handle_callback(update: dict) -> None:
    """Кнопка «Полный транскрипт»: отдаём очищенную расшифровку файлом."""
    from app.pipeline import load_pending_payload

    query = update.get("callback_query") or {}
    data = str(query.get("data") or "")
    if not data.startswith("tr:"):
        return
    meeting_id = data[3:]
    chat = str(query.get("message", {}).get("chat", {}).get("id") or "")
    _api("answerCallbackQuery", {"callback_query_id": query.get("id"), "text": "Готовлю транскрипт…"})
    try:
        payload = load_pending_payload(meeting_id)
        transcript, _ = build_claude_source_text(payload)
        cleaned = cached_clean_transcript(meeting_id, transcript)
    except Exception:
        logger.exception("Транскрипт для %s не собрался", meeting_id)
        _send(chat, "Не получилось собрать транскрипт. Возможно, встреча уже удалена из хранилища.")
        return
    name = re.sub(r"[^\w\-. ]+", "", (payload.title or "meeting"))[:50].strip() or "meeting"
    caption = f"📄 {payload.title or 'Встреча'} · {(payload.start_time or '')[:10]}\nФинансы и юридическое вырезаны."
    res = _api(
        "sendDocument",
        {"chat_id": chat, "caption": caption},
        files={"document": (f"{name}.txt", io.BytesIO(cleaned.encode("utf-8")), "text/plain")},
    )
    if res.status_code != 200:
        logger.error("Транскрипт не отправился: %s", res.text[:200])


def trigger(meeting_id: str, stage: str = "digest") -> None:
    """Запустить шаг отдельным вызовом, чтобы уложиться в лимит времени функции."""
    base = f"https://{settings.vercel_stable_domain}"
    try:
        httpx.post(
            f"{base}/internal/task-preview",
            json={"meeting_id": meeting_id, "stage": stage},
            headers={"x-internal-token": internal_token()},
            timeout=3,
        )
    except httpx.TimeoutException:
        pass  # ожидаемо: вызов продолжает работать сам
    except Exception:
        logger.exception("Не удалось запустить дайджест для %s", meeting_id)
