from __future__ import annotations

import asyncio
import logging
import re
import uuid
from typing import Any, Dict, List, Optional

import httpx

from app.config import settings
from app.kv import AllowedChatsStore, BriefHtmlStore, BriefStore, ChecklistStore, NotionPageStore, PendingMeetingStore, SubscriberStore
from app.models import ReadAIWebhookPayload
from app.meeting_tasks import MeetingTasksResult, ProjectTasks
from app.security import ProcessedMeetingsStore
from app.services.brief_html import markdown_brief_to_html
from app.services.html_utils import escape_html
from app.services.telegram_formatter import (
    build_add_to_notion_keyboard,
    build_meeting_pending_keyboard,
    build_process_meeting_keyboard,
    build_project_picker_keyboard,
    checklist_task_text,
    checklist_title,
    format_intro_message,
    format_meeting_pending_message,
    format_no_tasks_message,
    format_project_header,
    format_task_message,
)

logger = logging.getLogger(__name__)
TELEGRAM_MESSAGE_LIMIT = 4096
subscriber_store = SubscriberStore(settings.database_path)
allowed_store = AllowedChatsStore(settings.database_path)
checklist_store = ChecklistStore(settings.database_path)
pending_store = PendingMeetingStore(settings.database_path)
processed_store = ProcessedMeetingsStore(settings.database_path)
brief_store = BriefStore(settings.database_path)
brief_html_store = BriefHtmlStore(settings.database_path)
notion_page_store = NotionPageStore(settings.database_path)


def _api_url(method: str) -> str:
    return f"https://api.telegram.org/bot{settings.telegram_bot_token}/{method}"


def _is_private_chat(chat_id: str) -> bool:
    try:
        return int(chat_id) > 0
    except ValueError:
        return False


async def _telegram_post(
    client: httpx.AsyncClient,
    method: str,
    payload: Dict[str, Any],
    *,
    soft: bool = False,
) -> Dict[str, Any]:
    response = await client.post(_api_url(method), json=payload)

    if response.status_code == 403:
        chat_id = payload.get("chat_id")
        if chat_id is not None:
            subscriber_store.remove(str(chat_id))
            logger.info("Подписчик %s заблокировал бота — удалён из рассылки", chat_id)
        return {"ok": False, "blocked": True}

    try:
        response.raise_for_status()
        data = response.json()
    except Exception as exc:
        if soft:
            logger.warning("Telegram %s failed: %s", method, exc)
            return {"ok": False, "error": str(exc)}
        raise

    if not data.get("ok"):
        if soft:
            logger.warning("Telegram %s error: %s", method, data)
            return data
        raise RuntimeError(f"Telegram API error ({method}): {data}")
    return data


async def send_chat_action(chat_id: str, action: str = "typing") -> None:
    async with httpx.AsyncClient(timeout=15.0) as client:
        await _telegram_post(
            client,
            "sendChatAction",
            {"chat_id": chat_id, "action": action},
        )


async def send_message_draft(
    chat_id: str,
    draft_id: int,
    text: str,
    parse_mode: str = "HTML",
) -> None:
    async with httpx.AsyncClient(timeout=15.0) as client:
        await _telegram_post(
            client,
            "sendMessageDraft",
            {
                "chat_id": chat_id,
                "draft_id": draft_id,
                "text": text,
                "parse_mode": parse_mode,
            },
        )


async def send_message_to_chat(
    chat_id: str,
    text: str,
    *,
    parse_mode: Optional[str] = "HTML",
    reply_markup: Optional[Dict[str, Any]] = None,
    use_typewriter: bool = False,
) -> Optional[int]:
    chunks = _split_message(text)

    async with httpx.AsyncClient(timeout=30.0) as client:
        message_id: Optional[int] = None
        for index, chunk in enumerate(chunks):
            if use_typewriter and _is_private_chat(chat_id) and index == 0:
                await _stream_message_draft(chat_id, chunk, parse_mode)

            payload: Dict[str, Any] = {
                "chat_id": chat_id,
                "text": chunk,
                "disable_web_page_preview": True,
            }
            if parse_mode:
                payload["parse_mode"] = parse_mode
            if reply_markup and index == len(chunks) - 1:
                payload["reply_markup"] = reply_markup

            data = await _telegram_post(client, "sendMessage", payload)
            if data.get("blocked"):
                return None
            message_id = data.get("result", {}).get("message_id")

        return message_id


async def _stream_message_draft(chat_id: str, text: str, parse_mode: str) -> None:
    if not settings.telegram_typewriter:
        return

    draft_id = abs(hash(f"{chat_id}:{text[:32]}")) % 2_147_483_646 + 1
    parts = _draft_chunks(text)
    if not parts:
        return

    for part in parts:
        try:
            await send_message_draft(chat_id, draft_id, part, parse_mode)
        except Exception:
            logger.debug("sendMessageDraft недоступен для chat_id=%s", chat_id)
            return
        await asyncio.sleep(0.08)


def _draft_chunks(text: str, step: int = 80) -> List[str]:
    if len(text) <= step:
        return [text]

    chunks: List[str] = []
    for index in range(step, len(text) + step, step):
        chunks.append(text[:index])
    return chunks


async def send_native_checklist(
    chat_id: str,
    project: ProjectTasks,
) -> bool:
    tasks = [
        {
            "id": index,
            "text": checklist_task_text(task),
        }
        for index, task in enumerate(project.tasks, start=1)
    ]
    if not tasks:
        return False

    payload: Dict[str, Any] = {
        "chat_id": chat_id,
        "checklist": {
            "title": checklist_title(project.name),
            "tasks": tasks,
            "others_can_mark_tasks_as_done": True,
        },
    }
    if settings.telegram_business_connection_id:
        payload["business_connection_id"] = settings.telegram_business_connection_id

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            data = await _telegram_post(client, "sendChecklist", payload)
        except Exception:
            logger.info(
                "sendChecklist недоступен для chat_id=%s, используем inline-чек-лист",
                chat_id,
            )
            return False

        if data.get("blocked"):
            return False
        return True


async def send_interactive_checklist(
    chat_id: str,
    meeting_id: str,
    project: ProjectTasks,
) -> None:
    checklist_id = uuid.uuid4().hex[:10]
    titles = [
        checklist_task_text(task)
        for task in project.tasks
        if task.title.strip()
    ]
    if not titles:
        return

    checklist_store.save(
        checklist_id,
        {
            "chat_id": chat_id,
            "meeting_id": meeting_id,
            "project": project.name,
            "tasks": titles,
            "checked": [False] * len(titles),
        },
    )

    header = (
        f"✅ <b>{checklist_title(project.name)}</b>\n\n"
        "<i>Нажимайте на задачу, чтобы отметить выполнение.</i>"
    )
    message_id = await send_message_to_chat(
        chat_id,
        header,
        reply_markup=_build_checklist_keyboard(checklist_id, titles, [False] * len(titles)),
    )
    if message_id is not None:
        checklist_store.update_message_id(checklist_id, message_id)


def _build_checklist_keyboard(
    checklist_id: str,
    titles: List[str],
    checked: List[bool],
) -> Dict[str, Any]:
    rows: List[List[Dict[str, str]]] = []
    for index, title in enumerate(titles):
        mark = "✅" if checked[index] else "☐"
        label = f"{mark} {title}"
        if len(label) > 60:
            label = f"{mark} {title[:57].rstrip()}…"
        rows.append(
            [
                {
                    "text": label,
                    "callback_data": f"ct:{checklist_id}:{index}",
                }
            ]
        )
    return {"inline_keyboard": rows}


async def send_meeting_pending_notifications(payload: ReadAIWebhookPayload) -> None:
    project_name: Optional[str] = None
    show_project = bool(settings.notion_projects_database_id.strip())
    if show_project:
        try:
            from app.services.notion_projects import ensure_auto_project_assignment

            project_name = await ensure_auto_project_assignment(payload)
        except Exception:
            logger.exception("Не удалось определить проект для %s", payload.meeting_key)

    text = format_meeting_pending_message(
        payload,
        project_name=project_name,
        show_project=show_project,
    )
    keyboard = build_meeting_pending_keyboard(payload.meeting_key)
    chat_ids = _recipient_chat_ids()

    errors = 0
    for chat_id in chat_ids:
        try:
            await send_message_to_chat(
                chat_id,
                text,
                reply_markup=keyboard,
            )
        except Exception:
            errors += 1
            logger.exception(
                "Не удалось отправить уведомление о созвоне в chat_id=%s",
                chat_id,
            )

    if errors == len(chat_ids):
        raise RuntimeError("Не удалось отправить уведомление ни одному подписчику")


def _recipient_chat_ids() -> List[str]:
    chat_ids = allowed_store.list_chat_ids()
    if not chat_ids:
        raise RuntimeError(
            "Нет получателей. Укажите TELEGRAM_ADMIN_IDS и TELEGRAM_CHAT_ID в Vercel "
            "или добавьте пользователей командой /add."
        )
    return chat_ids


async def send_document_to_chat(
    chat_id: str,
    filename: str,
    content: str,
    *,
    caption: Optional[str] = None,
    mime_type: str = "text/plain; charset=utf-8",
    add_bom: bool = True,
) -> Optional[int]:
    payload: Dict[str, Any] = {"chat_id": chat_id}
    if caption:
        payload["caption"] = caption[:1024]

    file_bytes = content.encode("utf-8")
    if add_bom:
        file_bytes = b"\xef\xbb\xbf" + file_bytes
    files = {
        "document": (filename, file_bytes, mime_type),
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            _api_url("sendDocument"),
            data=payload,
            files=files,
        )

        if response.status_code == 403:
            subscriber_store.remove(chat_id)
            logger.info("Подписчик %s заблокировал бота — удалён из рассылки", chat_id)
            return None

        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram API error (sendDocument): {data}")
        return data.get("result", {}).get("message_id")


def _brief_filename(payload: ReadAIWebhookPayload, extension: str) -> str:
    title = payload.title or payload.meeting_key or "call"
    slug = re.sub(r"[^\w\s-]", "", title, flags=re.UNICODE)
    slug = re.sub(r"[-\s]+", "-", slug.strip()).strip("-")[:60] or "call"
    slug = slug.encode("ascii", "ignore").decode("ascii") or "call"
    date_part = "brief"
    if payload.start_time and "T" in payload.start_time:
        date_part = payload.start_time.split("T", 1)[0]
    return f"brief-{slug}-{date_part}.{extension}"


async def deliver_meeting_brief(
    payload: ReadAIWebhookPayload,
    brief_text: str,
    *,
    skip_intro: bool = False,
) -> None:
    chat_ids = _recipient_chat_ids()
    html_filename = _brief_filename(payload, "html")
    title = payload.title or "Командный звонок"
    html_content = markdown_brief_to_html(brief_text)
    meeting_id = payload.meeting_key

    errors = 0
    for chat_id in chat_ids:
        try:
            await send_chat_action(chat_id, "upload_document")
            if not skip_intro:
                await send_message_to_chat(
                    chat_id,
                    f"📎 Бриф по созвону: {title}",
                    parse_mode=None,
                )
            sent = await send_document_to_chat(
                chat_id,
                html_filename,
                html_content,
                caption=f"📋 Бриф: {title}",
                mime_type="text/html; charset=utf-8",
                add_bom=True,
            )
            if sent is None:
                errors += 1
            else:
                await send_notion_offer(chat_id, meeting_id, title)
        except Exception:
            errors += 1
            logger.exception("Не удалось отправить бриф в chat_id=%s", chat_id)

    if errors == len(chat_ids):
        raise RuntimeError("Не удалось отправить бриф ни одному подписчику")


async def send_notion_offer(chat_id: str, meeting_id: str, title: str) -> None:
    if not settings.notion_enabled:
        return

    existing_url = notion_page_store.load(meeting_id)
    if existing_url:
        await send_message_to_chat(
            chat_id,
            f"📝 Уже в Notion:\n{existing_url}",
            parse_mode=None,
        )
        return

    await send_message_to_chat(
        chat_id,
        f"Бриф «{title}» готов. Добавить в Notion?",
        reply_markup=build_add_to_notion_keyboard(meeting_id),
        parse_mode=None,
    )


def _split_brief(text: str, limit: int = TELEGRAM_MESSAGE_LIMIT) -> List[str]:
    if len(text) <= limit:
        return [text]

    parts = re.split(r"(?=\n## )", text)
    chunks: List[str] = []
    current = ""

    for part in parts:
        candidate = f"{current}{part}" if current else part
        if len(candidate) <= limit:
            current = candidate
            continue
        if current.strip():
            chunks.append(current.rstrip())
        if len(part) <= limit:
            current = part
            continue
        chunks.extend(_split_message(part, limit))
        current = ""

    if current.strip():
        chunks.append(current.rstrip())
    return chunks


async def deliver_meeting_tasks(
    payload: ReadAIWebhookPayload,
    result: MeetingTasksResult,
    *,
    skip_intro: bool = False,
) -> None:
    chat_ids = _recipient_chat_ids()

    errors = 0
    for chat_id in chat_ids:
        try:
            await _deliver_to_chat(chat_id, payload, result, skip_intro=skip_intro)
        except Exception:
            errors += 1
            logger.exception("Не удалось отправить отчёт в chat_id=%s", chat_id)

    if errors == len(chat_ids):
        raise RuntimeError("Не удалось отправить сообщение ни одному подписчику")


async def _deliver_to_chat(
    chat_id: str,
    payload: ReadAIWebhookPayload,
    result: MeetingTasksResult,
    *,
    skip_intro: bool = False,
) -> None:
    if not skip_intro:
        await send_chat_action(chat_id, "typing")
        intro = format_intro_message(payload, result)
        sent = await send_message_to_chat(
            chat_id,
            intro,
            use_typewriter=settings.telegram_typewriter_enabled,
        )
        if sent is None:
            return

    if not result.has_tasks or not result.projects:
        await send_message_to_chat(chat_id, format_no_tasks_message(result))
        return

    for project in result.projects:
        await send_message_to_chat(chat_id, format_project_header(project))

        for index, task in enumerate(project.tasks, start=1):
            await send_message_to_chat(
                chat_id,
                format_task_message(project.name, index, task),
            )

        native_sent = await send_native_checklist(chat_id, project)
        if not native_sent:
            await send_interactive_checklist(chat_id, payload.meeting_key, project)


async def send_telegram_message(text: str) -> None:
    chat_ids = _recipient_chat_ids()
    for chat_id in chat_ids:
        await send_message_to_chat(chat_id, text, parse_mode=None)


async def handle_telegram_callback(callback_query: Dict[str, Any]) -> None:
    data = (callback_query.get("data") or "").strip()
    if data.startswith("pm:"):
        await handle_process_meeting_callback(callback_query)
        return
    if data.startswith("no:"):
        await handle_add_to_notion_callback(callback_query)
        return
    if data.startswith("ct:"):
        await handle_checklist_callback(callback_query)
        return
    if data.startswith("pp:"):
        await handle_project_picker_callback(callback_query)
        return
    if data.startswith("ps:"):
        await handle_project_select_callback(callback_query)
        return
    if data.startswith("pb:"):
        await handle_project_back_callback(callback_query)
        return


async def handle_process_meeting_callback(callback_query: Dict[str, Any]) -> None:
    data = (callback_query.get("data") or "").strip()
    meeting_id = data.removeprefix("pm:").strip()
    if not meeting_id:
        await _answer_callback(callback_query, "Некорректная команда")
        return

    message = callback_query.get("message") or {}

    from app.pipeline import resolve_pending_meeting_id

    meeting_id = resolve_pending_meeting_id(meeting_id) or meeting_id

    if processed_store.is_processed(meeting_id):
        await _answer_callback(callback_query, "Задачи по этому созвону уже отправлены")
        await _edit_pending_message(callback_query, suffix="\n\n✅ <i>Задачи уже получены</i>")
        return

    if not pending_store.load(meeting_id):
        await _answer_callback(callback_query, "Данные созвона не найдены", show_alert=True)
        return

    if not pending_store.try_acquire_processing_lock(meeting_id):
        await _answer_callback(callback_query, "Уже обрабатывается…")
        return

    await _answer_callback(callback_query, "Обрабатываю транскрипцию…", soft=True)
    await _edit_pending_message(callback_query, suffix="\n\n⏳ <i>Обработка…</i>")

    try:
        from app.pipeline import process_meeting_by_id

        await process_meeting_by_id(meeting_id)
        await _edit_pending_message(callback_query, suffix="\n\n✅ <i>Бриф отправлен</i>")
    except Exception as exc:
        logger.exception("Ошибка обработки созвона %s", meeting_id)
        await _edit_pending_message(
            callback_query,
            suffix=f"\n\n❌ <i>Ошибка: {escape_html(str(exc)[:200])}</i>",
        )
        await _answer_callback(callback_query, "Не удалось обработать созвон", show_alert=True, soft=True)
    finally:
        pending_store.release_processing_lock(meeting_id)


async def handle_add_to_notion_callback(callback_query: Dict[str, Any]) -> None:
    data = (callback_query.get("data") or "").strip()
    meeting_id = data.removeprefix("no:").strip()
    if not meeting_id:
        await _answer_callback(callback_query, "Некорректная команда")
        return

    if not settings.notion_enabled:
        await _answer_callback(
            callback_query,
            "Notion не настроен",
            show_alert=True,
        )
        return

    existing_url = notion_page_store.load(meeting_id)
    if existing_url:
        await _answer_callback(callback_query, "Уже добавлено в Notion")
        await _edit_notion_offer_message(
            callback_query,
            f"📝 Страница в Notion:\n{existing_url}",
        )
        return

    brief_html = brief_html_store.load(meeting_id)
    brief_md = brief_store.load(meeting_id)
    if brief_md:
        brief_html = markdown_brief_to_html(brief_md)
        brief_html_store.save(meeting_id, brief_html)
    elif not brief_html:
        await _answer_callback(
            callback_query,
            "Сначала получите бриф по кнопке «Получить бриф»",
            show_alert=True,
        )
        return

    await _answer_callback(callback_query, "Создаю страницу в Notion…", soft=True)
    await _edit_notion_offer_message(callback_query, "⏳ <i>Добавляю в Notion…</i>")

    try:
        from app.pipeline import load_pending_payload
        from app.services.notion import (
            append_link_to_page,
            parse_page_title_from_html,
            publish_brief_html_to_notion,
        )
        from app.services.notion_projects import resolve_meeting_link_target

        payload = load_pending_payload(meeting_id)
        meeting_id = payload.meeting_key
        page_url = await publish_brief_html_to_notion(payload, brief_html)
        if not page_url:
            raise RuntimeError("Notion не вернул URL страницы")

        page_title = parse_page_title_from_html(brief_html, payload)
        notion_page_store.save(meeting_id, page_url)

        target_page_id, target_kind = await resolve_meeting_link_target(
            payload,
            brief_md or "",
        )
        await append_link_to_page(target_page_id, page_title, page_url)

        if target_kind.startswith("project:"):
            target_note = f"📁 {target_kind.removeprefix('project:')}"
        else:
            target_note = "📂 no project"

        await _edit_notion_offer_message(
            callback_query,
            f"✅ <b>Добавлено в Notion</b>\n{page_url}\n\n{target_note}",
            remove_keyboard=True,
        )
    except Exception as exc:
        logger.exception("Notion callback failed for %s", meeting_id)
        await _edit_notion_offer_message(
            callback_query,
            f"❌ <i>Не удалось добавить в Notion: {escape_html(str(exc)[:200])}</i>",
        )


async def _edit_notion_offer_message(
    callback_query: Dict[str, Any],
    text: str,
    *,
    remove_keyboard: bool = False,
) -> None:
    message = callback_query.get("message") or {}
    chat_id = (message.get("chat") or {}).get("id")
    message_id = message.get("message_id")
    if chat_id is None or message_id is None:
        return

    payload: Dict[str, Any] = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if remove_keyboard:
        payload["reply_markup"] = {"inline_keyboard": []}

    async with httpx.AsyncClient(timeout=15.0) as client:
        await _telegram_post(client, "editMessageText", payload, soft=True)


async def _edit_pending_message(
    callback_query: Dict[str, Any],
    *,
    suffix: str = "",
) -> None:
    message = callback_query.get("message") or {}
    chat_id = (message.get("chat") or {}).get("id")
    message_id = message.get("message_id")
    if chat_id is None or message_id is None:
        return

    text = message.get("text") or message.get("caption") or ""
    new_text = text
    if suffix and suffix.strip() not in text:
        new_text = f"{text}{suffix}"

    async with httpx.AsyncClient(timeout=15.0) as client:
        if suffix and new_text != text:
            await _telegram_post(
                client,
                "editMessageText",
                {
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "text": new_text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                    "reply_markup": {"inline_keyboard": []},
                },
                soft=True,
            )
        else:
            await _telegram_post(
                client,
                "editMessageReplyMarkup",
                {
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "reply_markup": {"inline_keyboard": []},
                },
                soft=True,
            )


async def handle_project_picker_callback(callback_query: Dict[str, Any]) -> None:
    data = (callback_query.get("data") or "").strip()
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != "pp":
        await _answer_callback(callback_query, "Некорректная команда")
        return

    meeting_id = parts[1].strip()
    try:
        page = int(parts[2])
    except ValueError:
        page = 0

    from app.pipeline import resolve_pending_meeting_id

    meeting_id = resolve_pending_meeting_id(meeting_id) or meeting_id

    if not settings.notion_projects_database_id.strip():
        await _answer_callback(callback_query, "Список проектов не настроен", show_alert=True)
        return

    if not pending_store.load(meeting_id):
        await _answer_callback(callback_query, "Данные созвона не найдены", show_alert=True)
        return

    from app.services.notion_projects import get_selectable_projects

    projects = get_selectable_projects()
    if not projects:
        await _answer_callback(callback_query, "Список проектов пуст", show_alert=True)
        return

    keyboard = build_project_picker_keyboard(meeting_id, projects, page=page)
    await _answer_callback(callback_query, "Выберите проект")
    await _edit_message_markup(callback_query, keyboard)


async def handle_project_select_callback(callback_query: Dict[str, Any]) -> None:
    data = (callback_query.get("data") or "").strip()
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != "ps":
        await _answer_callback(callback_query, "Некорректная команда")
        return

    meeting_id = parts[1].strip()
    selection = parts[2].strip()

    from app.pipeline import resolve_pending_meeting_id

    meeting_id = resolve_pending_meeting_id(meeting_id) or meeting_id

    if not pending_store.load(meeting_id):
        await _answer_callback(callback_query, "Данные созвона не найдены", show_alert=True)
        return

    from app.services.notion_projects import (
        general_notion_page_id,
        get_selectable_projects,
        save_manual_project_assignment,
    )

    if selection == "g":
        save_manual_project_assignment(
            meeting_id,
            page_id=general_notion_page_id(),
            name="no project",
            kind="general",
        )
        await _answer_callback(callback_query, "Проект: no project")
    else:
        try:
            index = int(selection)
        except ValueError:
            await _answer_callback(callback_query, "Некорректный выбор")
            return

        projects = get_selectable_projects()
        if index < 0 or index >= len(projects):
            await _answer_callback(callback_query, "Проект не найден", show_alert=True)
            return

        project = projects[index]
        save_manual_project_assignment(
            meeting_id,
            page_id=project.page_id,
            name=project.name,
            kind="project",
        )
        await _answer_callback(callback_query, f"Проект: {project.name}")

    await _refresh_pending_notification_message(callback_query, meeting_id)


async def handle_project_back_callback(callback_query: Dict[str, Any]) -> None:
    data = (callback_query.get("data") or "").strip()
    meeting_id = data.removeprefix("pb:").strip()
    if not meeting_id:
        await _answer_callback(callback_query, "Некорректная команда")
        return

    from app.pipeline import resolve_pending_meeting_id

    meeting_id = resolve_pending_meeting_id(meeting_id) or meeting_id
    await _answer_callback(callback_query, "Назад")
    await _refresh_pending_notification_message(callback_query, meeting_id)


async def _render_pending_notification(meeting_id: str) -> tuple[str, dict]:
    from app.pipeline import load_pending_payload
    from app.services.notion_projects import assignment_display_name, meeting_project_store

    payload = load_pending_payload(meeting_id)
    assignment = meeting_project_store.load(meeting_id)
    project_name = assignment_display_name(assignment)
    show_project = bool(settings.notion_projects_database_id.strip())
    text = format_meeting_pending_message(
        payload,
        project_name=project_name,
        show_project=show_project,
    )
    if assignment and assignment.get("source") == "manual":
        text = f"{text}\n\n<i>Проект выбран вручную.</i>"
    return text, build_meeting_pending_keyboard(meeting_id)


async def _refresh_pending_notification_message(
    callback_query: Dict[str, Any],
    meeting_id: str,
) -> None:
    message = callback_query.get("message") or {}
    chat_id = (message.get("chat") or {}).get("id")
    message_id = message.get("message_id")
    if chat_id is None or message_id is None:
        return

    try:
        text, keyboard = await _render_pending_notification(meeting_id)
    except Exception:
        logger.exception("Не удалось обновить уведомление для %s", meeting_id)
        return

    async with httpx.AsyncClient(timeout=15.0) as client:
        await _telegram_post(
            client,
            "editMessageText",
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
                "reply_markup": keyboard,
            },
            soft=True,
        )


async def _edit_message_markup(
    callback_query: Dict[str, Any],
    reply_markup: Dict[str, Any],
) -> None:
    message = callback_query.get("message") or {}
    chat_id = (message.get("chat") or {}).get("id")
    message_id = message.get("message_id")
    if chat_id is None or message_id is None:
        return

    async with httpx.AsyncClient(timeout=15.0) as client:
        await _telegram_post(
            client,
            "editMessageReplyMarkup",
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "reply_markup": reply_markup,
            },
            soft=True,
        )


async def handle_checklist_callback(callback_query: Dict[str, Any]) -> None:
    data = (callback_query.get("data") or "").strip()
    if not data.startswith("ct:"):
        return

    parts = data.split(":")
    if len(parts) != 3:
        return

    _, checklist_id, task_index_raw = parts
    try:
        task_index = int(task_index_raw)
    except ValueError:
        return

    state = checklist_store.load(checklist_id)
    if not state:
        await _answer_callback(callback_query, "Чек-лист устарел")
        return

    tasks = state.get("tasks") or []
    checked = state.get("checked") or [False] * len(tasks)
    if task_index < 0 or task_index >= len(tasks):
        await _answer_callback(callback_query, "Задача не найдена")
        return

    checked[task_index] = not checked[task_index]
    state["checked"] = checked
    checklist_store.save(checklist_id, state)

    message = callback_query.get("message") or {}
    chat_id = (message.get("chat") or {}).get("id")
    message_id = message.get("message_id")
    if chat_id is None or message_id is None:
        return

    async with httpx.AsyncClient(timeout=15.0) as client:
        await _telegram_post(
            client,
            "editMessageReplyMarkup",
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "reply_markup": _build_checklist_keyboard(checklist_id, tasks, checked),
            },
        )

    done_count = sum(1 for item in checked if item)
    await _answer_callback(
        callback_query,
        f"Отмечено {done_count} из {len(tasks)}",
        show_alert=False,
    )


async def _answer_callback(
    callback_query: Dict[str, Any],
    text: str,
    *,
    show_alert: bool = False,
    soft: bool = False,
) -> None:
    callback_id = callback_query.get("id")
    if not callback_id:
        return

    async with httpx.AsyncClient(timeout=15.0) as client:
        await _telegram_post(
            client,
            "answerCallbackQuery",
            {
                "callback_query_id": callback_id,
                "text": text,
                "show_alert": show_alert,
            },
            soft=soft,
        )


def _split_message(text: str, limit: int = TELEGRAM_MESSAGE_LIMIT) -> List[str]:
    if len(text) <= limit:
        return [text]

    chunks: List[str] = []
    remaining = text

    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break

        split_at = remaining.rfind("\n\n", 0, limit)
        if split_at == -1:
            split_at = remaining.rfind("\n", 0, limit)
        if split_at == -1:
            split_at = limit

        chunks.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip()

    return chunks
