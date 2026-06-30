from __future__ import annotations

import logging
import re
from typing import List

import anthropic

from app.config import settings
from app.models import ReadAIWebhookPayload
from app.services.transcript import build_claude_source_text, build_meeting_context

logger = logging.getLogger(__name__)


def load_system_prompt() -> str:
    path = settings.prompt_path
    if not path.exists():
        raise FileNotFoundError(f"Файл промпта не найден: {path}")
    return path.read_text(encoding="utf-8").strip()


def _build_user_message(payload: ReadAIWebhookPayload) -> str:
    source_text, source_kind = build_claude_source_text(payload)
    if not source_text:
        extra_keys = sorted((getattr(payload, "model_extra", None) or {}).keys())
        logger.error(
            "Нет текста для Claude. meeting=%s keys=%s extra=%s",
            payload.meeting_key,
            list(payload.model_dump(exclude_none=True).keys()),
            extra_keys,
        )
        raise ValueError("В webhook нет транскрипции и заметок")

    context = build_meeting_context(payload)
    if source_kind != "transcript":
        context += (
            "\nПримечание: полной транскрипции в webhook не было. "
            "Сформируй бриф по доступным материалам звонка."
        )

    return (
        "TRANSCRIPT:\n"
        f"{source_text}\n\n"
        "CONTEXT — project, known roles (who is client vs. agency), prior decisions:\n"
        f"{context}"
    )


def extract_meeting_brief(payload: ReadAIWebhookPayload) -> str:
    user_message = _build_user_message(payload)

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    response = client.messages.create(
        model=settings.claude_model,
        max_tokens=8192,
        system=load_system_prompt(),
        messages=[{"role": "user", "content": user_message}],
    )

    parts: List[str] = []
    for block in response.content:
        if block.type == "text":
            parts.append(block.text)

    raw = "\n".join(parts).strip()
    if not raw:
        raise ValueError("Claude вернул пустой ответ")

    return _strip_markdown_fence(raw)


def _strip_markdown_fence(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:markdown|md)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()
