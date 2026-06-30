from __future__ import annotations

from typing import Any, List, Optional

from pydantic import BaseModel, Field, field_validator


def _empty_if_none(value: Any) -> Any:
    if value is None:
        return ""
    return value


class TaskQuote(BaseModel):
    speaker: str = ""
    text: str = ""

    _coerce_strings = field_validator("speaker", "text", mode="before")(_empty_if_none)


class ExtractedTask(BaseModel):
    title: str
    status: Optional[str] = None
    is_exploration: bool = False
    priority: Optional[str] = None
    assignees: List[str] = Field(default_factory=list)
    summary: str = ""
    mechanics: List[str] = Field(default_factory=list)
    quotes: List[TaskQuote] = Field(default_factory=list)
    details: str = ""
    principles: List[str] = Field(default_factory=list)
    exploration_note: Optional[str] = None
    description: str = ""

    _coerce_strings = field_validator(
        "title",
        "summary",
        "details",
        "description",
        "exploration_note",
        mode="before",
    )(_empty_if_none)

    @field_validator("assignees", "mechanics", "principles", mode="before")
    @classmethod
    def none_to_list(cls, value: Any) -> List[str]:
        if value is None:
            return []
        return value

    @field_validator("quotes", mode="before")
    @classmethod
    def none_quotes_to_list(cls, value: Any) -> List[Any]:
        if value is None:
            return []
        return value


class ProjectTasks(BaseModel):
    name: str
    tasks: List[ExtractedTask] = Field(default_factory=list)

    _coerce_name = field_validator("name", mode="before")(_empty_if_none)

    @field_validator("tasks", mode="before")
    @classmethod
    def none_tasks_to_list(cls, value: Any) -> List[Any]:
        if value is None:
            return []
        return value


class MeetingTasksResult(BaseModel):
    meeting_title: Optional[str] = None
    meeting_date: Optional[str] = None
    meeting_time: Optional[str] = None
    recording_url: Optional[str] = None
    projects_discussed: List[str] = Field(default_factory=list)
    projects: List[ProjectTasks] = Field(default_factory=list)
    has_tasks: bool = True
    no_tasks_note: Optional[str] = None

    @field_validator("projects_discussed", "projects", mode="before")
    @classmethod
    def none_to_list(cls, value: Any) -> List[Any]:
        if value is None:
            return []
        return value
