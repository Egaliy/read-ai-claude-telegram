from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Union

from app.models import ReadAIWebhookPayload


def extract_transcript_text(payload: ReadAIWebhookPayload) -> str:
    chunks: List[str] = []

    if payload.transcript is not None:
        chunks.extend(_parse_transcript_value(payload.transcript))

    extra = getattr(payload, "model_extra", None) or {}
    for key in ("transcript", "transcripts", "full_transcript"):
        if key in extra and extra[key] is not payload.transcript:
            chunks.extend(_parse_transcript_value(extra[key]))

    return "\n".join(part for part in chunks if part).strip()


def build_fallback_source_text(payload: ReadAIWebhookPayload) -> str:
    parts: List[str] = []

    if payload.summary:
        parts.append(f"Summary:\n{payload.summary}")

    for label, items in (
        ("Action items", payload.action_items),
        ("Key questions", payload.key_questions),
        ("Topics", payload.topics),
        ("Chapters", payload.chapters or payload.chapter_summaries),
    ):
        formatted = _format_items(items)
        if formatted:
            parts.append(f"{label}:\n{formatted}")

    return "\n\n".join(parts).strip()


def extract_recording_url(payload: ReadAIWebhookPayload) -> str:
    extra = getattr(payload, "model_extra", None) or {}
    for key in (
        "report_url",
        "recording_url",
        "share_url",
        "meeting_url",
        "url",
        "link",
    ):
        value = extra.get(key) or getattr(payload, key, None)
        if isinstance(value, str) and value.strip().startswith("http"):
            return value.strip()
    return ""


def build_meeting_context(payload: ReadAIWebhookPayload) -> str:
    parts = [
        f"Название: {payload.title or 'Без названия'}",
        f"Начало: {payload.start_time or 'не указано'}",
        f"Конец: {payload.end_time or 'не указано'}",
    ]

    recording_url = extract_recording_url(payload)
    if recording_url:
        parts.append(f"Отчёт: {recording_url}")

    return "\n".join(parts)


def build_claude_source_text(payload: ReadAIWebhookPayload) -> tuple[str, str]:
    transcript = extract_transcript_text(payload)
    if transcript:
        return transcript, "transcript"

    fallback = build_fallback_source_text(payload)
    if fallback:
        return fallback, "summary_and_notes"

    return "", "empty"


def _parse_transcript_value(value: Any) -> List[str]:
    if value is None:
        return []

    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []

    if isinstance(value, list):
        lines: List[str] = []
        for item in value:
            lines.extend(_parse_transcript_value(item))
        return lines

    if isinstance(value, dict):
        if value.get("text") and isinstance(value["text"], str):
            text = value["text"].strip()
            if text:
                return [text]

        if value.get("turns"):
            return _parse_transcript_value(value["turns"])

        nested_lines: List[str] = []
        for key in ("speaker_blocks", "segments", "utterances", "items", "messages"):
            if key in value:
                nested_lines.extend(_parse_transcript_value(value[key]))
        if nested_lines:
            return nested_lines

        speaker = _speaker_name(value.get("speaker"))
        text = (
            value.get("text")
            or value.get("content")
            or value.get("words")
            or ""
        ).strip()
        if text:
            if speaker:
                return [f"[{speaker}]: {text}"]
            return [text]

        return []

    return []


def _format_items(items: Optional[List[Any]]) -> str:
    if not items:
        return ""

    lines: List[str] = []
    for item in items:
        if isinstance(item, str):
            lines.append(f"- {item.strip()}")
            continue
        if isinstance(item, dict):
            text = (
                item.get("text")
                or item.get("title")
                or item.get("summary")
                or item.get("content")
            )
            assignee = item.get("assignee") or item.get("owner")
            if text:
                suffix = f" ({assignee})" if assignee else ""
                lines.append(f"- {text}{suffix}")
                continue
            lines.append(f"- {json.dumps(item, ensure_ascii=False)}")
            continue
        lines.append(f"- {item}")

    return "\n".join(lines)


def _speaker_name(speaker: Optional[Union[Dict, str]]) -> str:
    if speaker is None:
        return ""
    if isinstance(speaker, str):
        return speaker
    return str(speaker.get("name") or speaker.get("email") or "")
