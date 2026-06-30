from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Field


class TranscriptTurn(BaseModel):
    speaker: Optional[Union[Dict[str, Any], str]] = None
    text: Optional[str] = None
    start_time_ms: Optional[int] = None
    end_time_ms: Optional[int] = None


class Transcript(BaseModel):
    speakers: Optional[List[Dict[str, Any]]] = None
    turns: Optional[List[TranscriptTurn]] = None
    text: Optional[str] = None


class TextItem(BaseModel):
    text: Optional[str] = None

    class Config:
        extra = "allow"


class ReadAIWebhookPayload(BaseModel):
    session_id: Optional[str] = None
    meeting_id: Optional[str] = None
    id: Optional[str] = None
    trigger: Optional[str] = None
    title: Optional[str] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    summary: Optional[str] = None
    action_items: Optional[List[Any]] = None
    key_questions: Optional[List[Any]] = None
    topics: Optional[List[Any]] = None
    chapters: Optional[List[Any]] = None
    chapter_summaries: Optional[List[Any]] = None
    transcript: Optional[Any] = None
    request_id: Optional[str] = None

    class Config:
        extra = "allow"

    @property
    def meeting_key(self) -> str:
        return (
            self.meeting_id
            or self.session_id
            or self.id
            or self.request_id
            or "unknown"
        )

    @property
    def storage_keys(self) -> List[str]:
        keys: List[str] = []
        for value in (self.meeting_id, self.session_id, self.id, self.request_id):
            if value and value not in keys:
                keys.append(value)
        return keys or ["unknown"]
