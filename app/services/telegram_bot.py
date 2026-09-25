from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

import httpx

from app.config import settings
from app.kv import AllowedChatsStore
from app.services.telegram import handle_telegram_callback, send_message_to_chat

logger = logging.getLogger(__name__)
allowed_store = AllowedChatsStore(settings.database_path)

WELCOME_TEXT = (
    "Вы в списке получателей.\n\n"
    "После каждого созвона Read AI сюда придёт уведомление с кнопкой "
    "«Получить бриф». Claude запустится только после нажатия.\n\n"
    "Чтобы отписаться — отправьте /stop"
)

ACCESS_DENIED_TEXT = (
    "Доступ закрыт.\n\n"
    "Бот работает только для приглашённых пользователей.\n"
    "Ваш chat id: {chat_id}\n\n"
    "Передайте его администратору для добавления."
)

UNSUBSCRIBE_TEXT = "Вы отписаны. Чтобы снова получать уведомления — попросите администратора добавить вас."

ADMIN_HELP_TEXT = (
    "Команды администратора:\n"
    "/add <chat_id> — добавить получателя\n"
    "/remove <chat_id> — убрать получателя\n"
    "/list — список получателей"
)


def _api_url(method: str) -> str:
    return f"https://api.telegram.org/bot{settings.telegram_bot_token}/{method}"


def is_admin(chat_id: str) -> bool:
    return chat_id in settings.admin_chat_ids


def is_allowed(chat_id: str) -> bool:
    return allowed_store.contains(chat_id)


def is_protected_chat(chat_id: str) -> bool:
    return chat_id in settings.seed_allowed_chat_ids


def seed_allowed_chats() -> None:
    for chat_id in settings.seed_allowed_chat_ids:
        allowed_store.add(chat_id)


async def handle_telegram_update(update: Dict[str, Any]) -> None:
    callback_query = update.get("callback_query")
    if callback_query:
        # Кнопка «Полный транскрипт» под дайджестом звонка.
        if str(callback_query.get("data") or "").startswith("tr:"):
            from app import task_preview

            await asyncio.to_thread(task_preview.handle_callback, update)
            return
        message = callback_query.get("message") or {}
        chat_id = (message.get("chat") or {}).get("id")
        if chat_id is not None and not is_allowed(str(chat_id)):
            from app.services.telegram import _answer_callback

            await _answer_callback(callback_query, "Нет доступа", show_alert=True)
            return
        await handle_telegram_callback(callback_query)
        return

    membership = update.get("my_chat_member")
    if membership:
        await _handle_membership_update(membership)
        return

    message = update.get("message") or update.get("edited_message")
    if not message:
        return

    from app import assistant, task_preview

    if await asyncio.to_thread(task_preview.handle_command, update):
        return

    # Вопрос ассистенту: в личке — любой текст, в группе — упоминание или ответ боту.
    bot_username = settings.telegram_bot_username or ""
    if await asyncio.to_thread(assistant.handle_question, message, bot_username):
        return

    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    text = (message.get("text") or "").strip()

    if chat_id is None or not text:
        return

    chat_id_str = str(chat_id)

    if text.startswith("/start"):
        await _handle_start(chat_id_str, chat)
        return

    if text.startswith("/stop"):
        await _handle_stop(chat_id_str)
        return

    if text.startswith("/add"):
        await _handle_add(chat_id_str, text)
        return

    if text.startswith("/remove"):
        await _handle_remove(chat_id_str, text)
        return

    if text.startswith("/list"):
        await _handle_list(chat_id_str)
        return


async def _handle_start(chat_id_str: str, chat: Dict[str, Any]) -> None:
    if not is_allowed(chat_id_str):
        try:
            await send_message_to_chat(
                chat_id_str,
                ACCESS_DENIED_TEXT.format(chat_id=chat_id_str),
                parse_mode=None,
            )
        except Exception:
            logger.exception("Не удалось отправить отказ доступа в chat_id=%s", chat_id_str)
        logger.info(
            "Отказ в доступе chat_id=%s (%s)",
            chat_id_str,
            chat.get("username") or chat.get("first_name") or "?",
        )
        return

    reply = WELCOME_TEXT
    if is_admin(chat_id_str):
        reply = f"{WELCOME_TEXT}\n\n{ADMIN_HELP_TEXT}"

    try:
        await send_message_to_chat(chat_id_str, reply, parse_mode=None)
    except Exception:
        logger.exception("Не удалось отправить welcome в chat_id=%s", chat_id_str)

    logger.info(
        "Разрешённый пользователь chat_id=%s (%s) отправил /start",
        chat_id_str,
        chat.get("username") or chat.get("first_name") or "?",
    )


async def _handle_stop(chat_id_str: str) -> None:
    if is_protected_chat(chat_id_str):
        try:
            await send_message_to_chat(
                chat_id_str,
                "Этот chat id задан в настройках бота и не может быть отключён через /stop.",
                parse_mode=None,
            )
        except Exception:
            logger.exception("Не удалось отправить ответ в chat_id=%s", chat_id_str)
        return

    if not is_allowed(chat_id_str):
        try:
            await send_message_to_chat(
                chat_id_str,
                ACCESS_DENIED_TEXT.format(chat_id=chat_id_str),
                parse_mode=None,
            )
        except Exception:
            logger.exception("Не удалось отправить ответ в chat_id=%s", chat_id_str)
        return

    allowed_store.remove(chat_id_str)
    try:
        await send_message_to_chat(chat_id_str, UNSUBSCRIBE_TEXT, parse_mode=None)
    except Exception:
        logger.exception("Не удалось отправить сообщение об отписке в chat_id=%s", chat_id_str)
    logger.info("Получатель %s отписался", chat_id_str)


async def _handle_add(chat_id_str: str, text: str) -> None:
    if not is_admin(chat_id_str):
        try:
            await send_message_to_chat(
                chat_id_str,
                ACCESS_DENIED_TEXT.format(chat_id=chat_id_str),
                parse_mode=None,
            )
        except Exception:
            logger.exception("Не удалось отправить отказ доступа в chat_id=%s", chat_id_str)
        return

    parts = text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        try:
            await send_message_to_chat(
                chat_id_str,
                "Использование: /add <chat_id>\n\nПример: /add 123456789",
                parse_mode=None,
            )
        except Exception:
            logger.exception("Не удалось отправить help в chat_id=%s", chat_id_str)
        return

    target_id = parts[1].strip()
    is_new = allowed_store.add(target_id)
    reply = (
        f"Получатель {target_id} добавлен."
        if is_new
        else f"Получатель {target_id} уже в списке."
    )
    try:
        await send_message_to_chat(chat_id_str, reply, parse_mode=None)
    except Exception:
        logger.exception("Не удалось отправить ответ /add в chat_id=%s", chat_id_str)
    logger.info("Админ %s добавил получателя %s", chat_id_str, target_id)


async def _handle_remove(chat_id_str: str, text: str) -> None:
    if not is_admin(chat_id_str):
        try:
            await send_message_to_chat(
                chat_id_str,
                ACCESS_DENIED_TEXT.format(chat_id=chat_id_str),
                parse_mode=None,
            )
        except Exception:
            logger.exception("Не удалось отправить отказ доступа в chat_id=%s", chat_id_str)
        return

    parts = text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        try:
            await send_message_to_chat(
                chat_id_str,
                "Использование: /remove <chat_id>",
                parse_mode=None,
            )
        except Exception:
            logger.exception("Не удалось отправить help в chat_id=%s", chat_id_str)
        return

    target_id = parts[1].strip()
    if is_protected_chat(target_id):
        try:
            await send_message_to_chat(
                chat_id_str,
                f"Chat id {target_id} задан в env и не удаляется через /remove.",
                parse_mode=None,
            )
        except Exception:
            logger.exception("Не удалось отправить ответ /remove в chat_id=%s", chat_id_str)
        return

    if not allowed_store.contains(target_id):
        try:
            await send_message_to_chat(
                chat_id_str,
                f"Получатель {target_id} не найден в списке.",
                parse_mode=None,
            )
        except Exception:
            logger.exception("Не удалось отправить ответ /remove в chat_id=%s", chat_id_str)
        return

    allowed_store.remove(target_id)
    try:
        await send_message_to_chat(chat_id_str, f"Получатель {target_id} удалён.", parse_mode=None)
    except Exception:
        logger.exception("Не удалось отправить ответ /remove в chat_id=%s", chat_id_str)
    logger.info("Админ %s удалил получателя %s", chat_id_str, target_id)


async def _handle_list(chat_id_str: str) -> None:
    if not is_admin(chat_id_str):
        try:
            await send_message_to_chat(
                chat_id_str,
                ACCESS_DENIED_TEXT.format(chat_id=chat_id_str),
                parse_mode=None,
            )
        except Exception:
            logger.exception("Не удалось отправить отказ доступа в chat_id=%s", chat_id_str)
        return

    chat_ids = allowed_store.list_chat_ids()
    if not chat_ids:
        reply = "Список получателей пуст."
    else:
        lines = [f"• {chat_id}" for chat_id in sorted(chat_ids)]
        reply = "Получатели:\n" + "\n".join(lines)

    try:
        await send_message_to_chat(chat_id_str, reply, parse_mode=None)
    except Exception:
        logger.exception("Не удалось отправить /list в chat_id=%s", chat_id_str)


async def _handle_membership_update(membership: Dict[str, Any]) -> None:
    chat = membership.get("chat") or {}
    chat_id = chat.get("id")
    if chat_id is None:
        return

    new_status = (membership.get("new_chat_member") or {}).get("status")
    if new_status not in {"member", "administrator"}:
        return

    chat_id_str = str(chat_id)
    title = chat.get("title") or chat.get("username") or chat.get("first_name")

    if not is_allowed(chat_id_str):
        logger.info(
            "Бот добавлен в неразрешённый чат %s (%s) — нужен /add от администратора",
            chat_id_str,
            title or "?",
        )
        if chat.get("type") in {"group", "supergroup"}:
            try:
                await send_message_to_chat(
                    chat_id_str,
                    "Бот не активирован для этой группы.\n\n"
                    f"Администратор должен выполнить в личке с ботом:\n/add {chat_id_str}",
                    parse_mode=None,
                )
            except Exception:
                logger.exception("Не удалось отправить сообщение в группу %s", chat_id_str)
        return

    logger.info("Бот активен в разрешённом чате %s (%s)", chat_id_str, title or "?")


async def fetch_updates(offset: Optional[int] = None) -> list:
    params: Dict[str, Any] = {"timeout": 30}
    if offset is not None:
        params["offset"] = offset

    async with httpx.AsyncClient(timeout=40.0) as client:
        response = await client.get(_api_url("getUpdates"), params=params)
        response.raise_for_status()
        data = response.json()

    if not data.get("ok"):
        raise RuntimeError(f"Telegram getUpdates error: {data}")

    return data.get("result", [])


async def delete_webhook() -> None:
    async with httpx.AsyncClient(timeout=15.0) as client:
        await client.get(_api_url("deleteWebhook"))


async def set_webhook(url: str) -> None:
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            _api_url("setWebhook"),
            json={
                "url": url,
                "allowed_updates": ["message", "my_chat_member", "callback_query"],
            },
        )
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram setWebhook error: {data}")


async def telegram_polling_loop() -> None:
    await delete_webhook()
    offset: Optional[int] = None
    logger.info("Telegram polling запущен")

    while True:
        try:
            updates = await fetch_updates(offset)
            for update in updates:
                await handle_telegram_update(update)
                offset = update["update_id"] + 1
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Ошибка Telegram polling")
            await asyncio.sleep(5)
