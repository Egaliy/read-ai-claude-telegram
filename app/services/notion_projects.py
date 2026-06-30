from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional, Tuple

import httpx

from app.config import settings
from app.kv import MeetingProjectStore, NotionProjectsCacheStore
from app.models import ReadAIWebhookPayload
from app.services.notion import NOTION_API, _headers, _normalize_id, _page_url
from app.services.transcript import build_claude_source_text

logger = logging.getLogger(__name__)

_MIN_PROJECT_NAME_LEN = 3
_CACHE_STALE_HOURS = 25

project_cache_store = NotionProjectsCacheStore(settings.database_path)
meeting_project_store = MeetingProjectStore(settings.database_path)


@dataclass(frozen=True)
class NotionProject:
    name: str
    page_id: str

    @property
    def url(self) -> str:
        return _page_url(self.page_id)


def _normalize_text(text: str) -> str:
    lowered = unicodedata.normalize("NFKC", text or "").casefold()
    lowered = re.sub(r"[^\w\s&./-]+", " ", lowered, flags=re.UNICODE)
    return re.sub(r"\s+", " ", lowered).strip()


def find_matching_projects(projects: List[NotionProject], *texts: str) -> List[NotionProject]:
    haystack = _normalize_text(" ".join(part for part in texts if part))
    if not haystack:
        return []

    matched: List[NotionProject] = []
    seen_ids: set[str] = set()

    for project in sorted(projects, key=lambda item: len(item.name), reverse=True):
        name = project.name.strip()
        if len(name) < _MIN_PROJECT_NAME_LEN:
            continue
        if _normalize_text(name) not in haystack:
            continue
        if project.page_id in seen_ids:
            continue
        seen_ids.add(project.page_id)
        matched.append(project)

    return matched


async def fetch_notion_projects(database_id: str) -> List[NotionProject]:
    db_id = _normalize_id(database_id.strip())
    projects: List[NotionProject] = []
    cursor: Optional[str] = None

    async with httpx.AsyncClient(timeout=30.0) as client:
        while True:
            body = {"page_size": 100}
            if cursor:
                body["start_cursor"] = cursor

            response = await client.post(
                f"{NOTION_API}/databases/{db_id}/query",
                headers=_headers(),
                json=body,
            )
            if response.status_code >= 400:
                raise RuntimeError(f"Notion projects query failed: {response.text[:500]}")

            data = response.json()
            for page in data.get("results", []):
                name = _extract_page_title(page)
                if not name.strip():
                    continue
                projects.append(NotionProject(name=name.strip(), page_id=page["id"]))

            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")

    return projects


def _extract_page_title(page: dict) -> str:
    for prop in (page.get("properties") or {}).values():
        if prop.get("type") != "title":
            continue
        chunks = prop.get("title") or []
        return "".join(part.get("plain_text", "") for part in chunks)
    return ""


def _projects_from_cache(raw: Optional[dict]) -> List[NotionProject]:
    if not raw:
        return []
    projects: List[NotionProject] = []
    for item in raw.get("projects", []):
        name = str(item.get("name") or "").strip()
        page_id = str(item.get("page_id") or "").strip()
        if name and page_id:
            projects.append(NotionProject(name=name, page_id=page_id))
    return projects


def cache_is_stale(raw: Optional[dict]) -> bool:
    if not raw:
        return True
    synced_at = raw.get("synced_at")
    if not synced_at:
        return True
    try:
        synced = datetime.fromisoformat(str(synced_at).replace("Z", "+00:00"))
    except ValueError:
        return True
    age_hours = (datetime.now(timezone.utc) - synced.astimezone(timezone.utc)).total_seconds() / 3600
    return age_hours >= _CACHE_STALE_HOURS


async def sync_notion_projects_cache() -> dict:
    """Загрузить Project Base из Notion и сохранить в Redis."""
    database_id = settings.notion_projects_database_id.strip()
    if not database_id:
        return {"synced": False, "reason": "NOTION_PROJECTS_DATABASE_ID is not set"}

    projects = await fetch_notion_projects(database_id)
    payload = {
        "synced_at": datetime.now(timezone.utc).isoformat(),
        "projects": [
            {"name": project.name, "page_id": project.page_id}
            for project in projects
        ],
    }
    project_cache_store.save(payload)
    logger.info("Notion projects cache synced: %s projects", len(projects))
    return {
        "synced": True,
        "count": len(projects),
        "synced_at": payload["synced_at"],
    }


def get_cached_notion_projects() -> List[NotionProject]:
    return _projects_from_cache(project_cache_store.load())


async def get_notion_projects(*, force_refresh: bool = False) -> List[NotionProject]:
    """Проекты из кэша; если кэш пустой или устарел — обновить из Notion."""
    raw = project_cache_store.load()
    if not force_refresh and raw and not cache_is_stale(raw):
        return _projects_from_cache(raw)

    await sync_notion_projects_cache()
    return get_cached_notion_projects()


def general_notion_page_id() -> str:
    return (
        settings.notion_general_page_id.strip()
        or settings.notion_parent_page_id.strip()
    )


def get_selectable_projects() -> List[NotionProject]:
    general_id = _normalize_id(general_notion_page_id())
    projects: List[NotionProject] = []
    for project in get_cached_notion_projects():
        if _normalize_id(project.page_id) == general_id:
            continue
        if _normalize_text(project.name) == _normalize_text("no project"):
            continue
        projects.append(project)
    return sorted(projects, key=lambda item: item.name.casefold())


def assignment_display_name(assignment: Optional[dict]) -> Optional[str]:
    if not assignment:
        return None
    if assignment.get("kind") == "general":
        return None
    name = str(assignment.get("name") or "").strip()
    return name or None


def save_manual_project_assignment(
    meeting_id: str,
    *,
    page_id: str,
    name: str,
    kind: str,
) -> None:
    meeting_project_store.save(
        meeting_id,
        {
            "page_id": page_id,
            "name": name,
            "kind": kind,
            "source": "manual",
        },
    )


async def ensure_auto_project_assignment(payload: ReadAIWebhookPayload) -> Optional[str]:
    """Автоопределение проекта при уведомлении; ручной выбор не перезаписывается."""
    meeting_id = payload.meeting_key
    existing = meeting_project_store.load(meeting_id)
    if existing and existing.get("source") == "manual":
        return assignment_display_name(existing)

    if not settings.notion_projects_database_id.strip():
        return None

    general_id = general_notion_page_id()
    try:
        project = await detect_meeting_project(payload)
    except Exception:
        logger.exception("Не удалось определить проект для %s", meeting_id)
        project = None

    if project:
        meeting_project_store.save(
            meeting_id,
            {
                "page_id": project.page_id,
                "name": project.name,
                "kind": "project",
                "source": "auto",
            },
        )
        return project.name

    meeting_project_store.save(
        meeting_id,
        {
            "page_id": general_id,
            "name": "no project",
            "kind": "general",
            "source": "auto",
        },
    )
    return None


def projects_cache_status() -> dict:
    raw = project_cache_store.load()
    projects = _projects_from_cache(raw)
    return {
        "count": len(projects),
        "synced_at": raw.get("synced_at") if raw else None,
        "stale": cache_is_stale(raw),
    }


def collect_meeting_search_text(
    payload: ReadAIWebhookPayload,
    brief_markdown: str = "",
) -> Tuple[str, ...]:
    source_text, _ = build_claude_source_text(payload)
    transcript_excerpt = source_text[:12000] if source_text else ""
    return (
        payload.title or "",
        brief_markdown,
        transcript_excerpt,
    )


async def detect_meeting_project(
    payload: ReadAIWebhookPayload,
    brief_markdown: str = "",
) -> Optional[NotionProject]:
    projects = await get_notion_projects()
    matched = find_matching_projects(
        projects,
        *collect_meeting_search_text(payload, brief_markdown),
    )
    if len(matched) == 1:
        return matched[0]
    return None


async def resolve_meeting_link_target(
    payload: ReadAIWebhookPayload,
    brief_markdown: str = "",
) -> Tuple[str, str]:
    """
    Выбрать страницу Notion для ссылки на бриф/расшифровку.

    Ручной или авто выбор из Redis → страница проекта или no project.
    Иначе fallback: один проект в тексте → проект, иначе general.
    """
    database_id = settings.notion_projects_database_id.strip()
    if not database_id:
        return settings.notion_parent_page_id.strip(), "hub"

    general_page_id = general_notion_page_id()
    meeting_id = payload.meeting_key
    assignment = meeting_project_store.load(meeting_id)
    if assignment and assignment.get("page_id"):
        page_id = str(assignment["page_id"])
        if assignment.get("kind") == "project":
            name = str(assignment.get("name") or "").strip()
            logger.info(
                "Notion link target: assigned project %r (%s, %s)",
                name,
                page_id,
                assignment.get("source"),
            )
            return page_id, f"project:{name}"
        logger.info(
            "Notion link target: assigned general (%s, %s)",
            page_id,
            assignment.get("source"),
        )
        return page_id, "general"

    projects = await get_notion_projects()
    matched = find_matching_projects(
        projects,
        *collect_meeting_search_text(payload, brief_markdown),
    )

    if len(matched) == 1:
        project = matched[0]
        logger.info(
            "Notion link target: project %r (%s)",
            project.name,
            project.page_id,
        )
        return project.page_id, f"project:{project.name}"

    if len(matched) > 1:
        names = ", ".join(project.name for project in matched)
        logger.info(
            "Notion link target: general (multiple projects: %s)",
            names,
        )
    else:
        logger.info("Notion link target: general (no project match)")

    return general_page_id, "general"
