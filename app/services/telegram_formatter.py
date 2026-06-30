from __future__ import annotations

from typing import List, Optional

from app.models import ReadAIWebhookPayload
from app.meeting_tasks import ExtractedTask, MeetingTasksResult, ProjectTasks
from app.services.html_utils import escape_html

PROJECTS_PER_PAGE = 8

_STATUS_LABELS = {
    "done": "✅ решено",
    "in_progress": "🔄 в работе",
    "risk": "⚠️",
}


def build_add_to_notion_keyboard(meeting_id: str) -> dict:
    return {
        "inline_keyboard": [
            [
                {
                    "text": "📝 Добавить в Notion",
                    "callback_data": f"no:{meeting_id}",
                }
            ]
        ]
    }


def format_meeting_pending_message(
    payload: ReadAIWebhookPayload,
    *,
    project_name: Optional[str] = None,
    show_project: bool = False,
) -> str:
    title = payload.title or "Командный звонок"
    date = _format_date(payload)
    time = _format_time(payload)

    lines = [
        "📞 <b>Прошёл созвон</b>",
        "",
        f"<b>Название:</b> {escape_html(title)}",
    ]

    if date:
        lines.append(f"<b>Дата:</b> {escape_html(date)}")
    if time:
        lines.append(f"<b>Время:</b> {escape_html(time)}")

    if show_project:
        if project_name:
            lines.append(f"<b>Проект:</b> {escape_html(project_name)}")
        else:
            lines.append("<b>Проект:</b> <i>no project</i>")

    lines.extend(
        [
            "",
            "<i>Нажмите кнопку — бриф придёт .html файлом.</i>",
        ]
    )
    return "\n".join(lines)


def build_meeting_pending_keyboard(meeting_id: str) -> dict:
    rows = [
        [
            {
                "text": "📋 Получить бриф",
                "callback_data": f"pm:{meeting_id}",
            }
        ],
    ]
    if _project_picker_enabled():
        rows.append(
            [
                {
                    "text": "📁 Поменять проект",
                    "callback_data": f"pp:{meeting_id}:0",
                }
            ]
        )
    return {"inline_keyboard": rows}


def build_process_meeting_keyboard(meeting_id: str) -> dict:
    return build_meeting_pending_keyboard(meeting_id)


def build_project_picker_keyboard(
    meeting_id: str,
    projects: List,
    *,
    page: int = 0,
) -> dict:
    total = len(projects)
    total_pages = max(1, (total + PROJECTS_PER_PAGE - 1) // PROJECTS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    start = page * PROJECTS_PER_PAGE
    chunk = projects[start : start + PROJECTS_PER_PAGE]

    rows: List[List[dict]] = []
    row: List[dict] = []
    for offset, project in enumerate(chunk):
        index = start + offset
        row.append(
            {
                "text": _truncate_button_label(project.name),
                "callback_data": f"ps:{meeting_id}:{index}",
            }
        )
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    rows.append(
        [
            {
                "text": "📂 no project",
                "callback_data": f"ps:{meeting_id}:g",
            }
        ]
    )

    nav: List[dict] = []
    if page > 0:
        nav.append({"text": "◀️", "callback_data": f"pp:{meeting_id}:{page - 1}"})
    if page < total_pages - 1:
        nav.append({"text": "▶️", "callback_data": f"pp:{meeting_id}:{page + 1}"})
    if nav:
        rows.append(nav)

    rows.append([{"text": "← Назад", "callback_data": f"pb:{meeting_id}"}])
    return {"inline_keyboard": rows}


def _project_picker_enabled() -> bool:
    from app.config import settings

    return bool(settings.notion_projects_database_id.strip())


def _truncate_button_label(text: str, max_len: int = 32) -> str:
    cleaned = (text or "").strip()
    if len(cleaned) <= max_len:
        return cleaned
    return f"{cleaned[: max_len - 1].rstrip()}…"


def format_intro_message(
    payload: ReadAIWebhookPayload,
    result: MeetingTasksResult,
) -> str:
    title = payload.title or result.meeting_title or "Командный звонок"
    date = result.meeting_date or _format_date(payload)
    time = result.meeting_time or _format_time(payload)
    project_names = _project_names(result)
    lines = [
        "📞 <b>Созвон завершён</b>",
        "",
        f"<b>Название:</b> {escape_html(title)}",
    ]

    if date:
        lines.append(f"<b>Дата:</b> {escape_html(date)}")
    if time:
        lines.append(f"<b>Время:</b> {escape_html(time)}")
    if project_names:
        lines.append(f"<b>Проекты:</b> {escape_html(', '.join(project_names))}")

    return "\n".join(lines)


def format_project_header(project: ProjectTasks) -> str:
    return f"📋 <b>Задачи · {escape_html(project.name)}</b>"


def format_task_message(
    project_name: str,
    index: int,
    task: ExtractedTask,
) -> str:
    lines = [_format_task_title(index, task)]

    assignees = ", ".join(name.strip() for name in task.assignees if name.strip())
    if assignees:
        lines.append(f"<i>Исполнители:</i> {escape_html(assignees)}")
    lines.append("")

    if task.is_exploration and (task.exploration_note or "").strip():
        lines.append(f"<i>{escape_html(task.exploration_note.strip())}</i>")
        lines.append("")

    summary = task.summary.strip() or task.description.strip()
    if summary:
        lines.append(escape_html(summary))
        lines.append("")

    quote_lines = _format_quotes(task.quotes)
    if quote_lines:
        lines.extend(quote_lines)
        lines.append("")

    if task.mechanics:
        lines.append("<b>Механика:</b>")
        for step_index, step in enumerate(task.mechanics, start=1):
            lines.append(f"{step_index}. {escape_html(step.strip())}")
        lines.append("")

    if task.details.strip():
        lines.append(f"<b>Детали:</b> {escape_html(task.details.strip())}")
        lines.append("")

    if task.principles:
        lines.append("<b>Принципы:</b>")
        for principle in task.principles:
            cleaned = principle.strip()
            if cleaned:
                lines.append(f"• {escape_html(cleaned)}")
        lines.append("")

    return "\n".join(lines).rstrip()


def format_project_tasks_message(project: ProjectTasks) -> str:
    lines = [format_project_header(project), ""]

    for index, task in enumerate(project.tasks, start=1):
        lines.append(format_task_message(project.name, index, task))
        lines.append("")

    return "\n".join(lines).rstrip()


def format_no_tasks_message(result: MeetingTasksResult) -> str:
    note = (result.no_tasks_note or "Обсуждение без конкретных action items.").strip()
    return (
        "📋 <b>Задачи по итогам созвона</b>\n\n"
        "<i>Задач не выявлено.</i>\n"
        f"{escape_html(note)}"
    )


def checklist_title(project_name: str) -> str:
    return f"Чек-лист · {project_name}"


def checklist_task_text(task: ExtractedTask, max_len: int = 100) -> str:
    prefix = "[эксплор] " if task.is_exploration else ""
    text = f"{prefix}{task.title.strip()}"
    if len(text) <= max_len:
        return text
    return f"{text[: max_len - 1].rstrip()}…"


def _format_task_title(index: int, task: ExtractedTask) -> str:
    title = task.title.strip()
    if task.is_exploration:
        title = f"[ЭКСПЛОРЕЙШН] {title}"

    status = _STATUS_LABELS.get((task.status or "").strip(), "")
    if status and task.priority:
        suffix = f" — {status} ({escape_html(task.priority.strip())})"
    elif status:
        suffix = f" — {status}"
    elif task.priority:
        suffix = f" — {escape_html(task.priority.strip())}"
    else:
        suffix = ""

    return f"<b>{index}. {escape_html(title)}{suffix}</b>"


def _format_quotes(quotes: List) -> List[str]:
    lines: List[str] = []
    for quote in quotes:
        speaker = (quote.speaker or "").strip()
        text = (quote.text or "").strip()
        if not text:
            continue
        if speaker:
            lines.append(f'<i>{escape_html(speaker)}:</i> "{escape_html(text)}"')
        else:
            lines.append(f'"{escape_html(text)}"')
    return lines


def _project_names(result: MeetingTasksResult) -> List[str]:
    names: List[str] = []
    seen = set()
    for name in result.projects_discussed:
        cleaned = name.strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            names.append(cleaned)
    for project in result.projects:
        cleaned = project.name.strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            names.append(cleaned)
    return names


def _format_date(payload: ReadAIWebhookPayload) -> Optional[str]:
    if not payload.start_time:
        return None
    return payload.start_time.split("T")[0]


def _format_time(payload: ReadAIWebhookPayload) -> Optional[str]:
    if not payload.start_time:
        return None
    if "T" not in payload.start_time:
        return None
    time_part = payload.start_time.split("T", 1)[1]
    return time_part.replace("Z", "").split(".")[0][:5]
