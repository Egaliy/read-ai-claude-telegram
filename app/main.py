from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from app.config import settings
from app.kv import AllowedChatsStore, SubscriberStore
from app.models import ReadAIWebhookPayload
from app import task_preview
from app.pipeline import load_pending_payload, process_meeting, queue_meeting
from app.security import verify_readai_signature
from app.services.telegram_bot import (
    handle_telegram_update,
    seed_allowed_chats,
    set_webhook,
    telegram_polling_loop,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)
subscriber_store = SubscriberStore(settings.database_path)
allowed_store = AllowedChatsStore(settings.database_path)


@asynccontextmanager
async def lifespan(_: FastAPI):
    missing = settings.validate_runtime()
    polling_task: Optional[asyncio.Task] = None

    if missing:
        logger.warning(
            "Не заданы переменные окружения: %s.",
            ", ".join(missing),
        )
    elif not settings.readai_webhook_signing_key:
        logger.warning(
            "READAI_WEBHOOK_SIGNING_KEY не задан — проверка подписи webhook отключена"
        )

    if settings.telegram_bot_token and not missing:
        seed_allowed_chats()
        webhook_url = settings.resolved_telegram_webhook_url
        if webhook_url:
            await set_webhook(webhook_url)
            logger.info("Telegram webhook установлен: %s", webhook_url)
        elif settings.use_polling:
            polling_task = asyncio.create_task(telegram_polling_loop())

        if settings.notion_projects_database_id.strip():
            from app.services.notion_projects import (
                get_cached_notion_projects,
                sync_notion_projects_cache,
            )

            if not get_cached_notion_projects():
                try:
                    result = await sync_notion_projects_cache()
                    logger.info("Notion projects initial sync: %s", result)
                except Exception:
                    logger.exception("Notion projects initial sync failed")

    try:
        yield
    finally:
        if polling_task:
            polling_task.cancel()
            try:
                await polling_task
            except asyncio.CancelledError:
                pass


app = FastAPI(
    title="Read AI → Claude → Telegram",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/")
async def root() -> dict:
    return {
        "service": "Read AI → Claude → Telegram",
        "health": "/health",
        "readai_webhook": "/webhooks/readai",
        "telegram_webhook": "/webhooks/telegram",
    }


@app.get("/health")
async def health() -> dict:
    missing = settings.validate_runtime()
    return {
        "status": "ok" if not missing else "missing_config",
        "missing_env": missing,
        "prompt_file_exists": settings.prompt_path.exists(),
        "telegram_subscribers": subscriber_store.count(),
        "telegram_allowed": allowed_store.count(),
        "telegram_admin_ids": settings.admin_chat_ids,
        "fixed_chat_ids": settings.fixed_chat_ids,
        "storage_backend": settings.storage_backend,
        "storage_warning": (
            "На Vercel без Redis подписчики не сохраняются. "
            "Подключите Redis Cloud или Upstash."
            if settings.is_vercel and settings.storage_backend == "sqlite"
            else None
        ),
        "telegram_mode": (
            "webhook"
            if settings.resolved_telegram_webhook_url
            else ("polling" if settings.use_polling else "disabled")
        ),
        "readai_webhook_url_hint": _readai_webhook_url_hint(),
        "notion_enabled": settings.notion_enabled,
        "brief_format": "html",
        "production_url": settings.resolved_public_base_url or None,
        "notion_projects": _notion_projects_status(),
    }


def _notion_projects_status() -> Optional[dict]:
    if not settings.notion_projects_database_id.strip():
        return None
    from app.services.notion_projects import projects_cache_status

    return projects_cache_status()


def _readai_webhook_url_hint() -> str:
    base = settings.resolved_public_base_url
    if base:
        return f"{base}/webhooks/readai"
    vercel_url = settings.resolved_telegram_webhook_url.replace(
        "/webhooks/telegram",
        "/webhooks/readai",
    )
    if vercel_url:
        return vercel_url
    return "https://read-ai-claude-telegram.vercel.app/webhooks/readai"


@app.post("/webhooks/readai")
async def readai_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_read_signature: Optional[str] = Header(default=None, alias="X-Read-Signature"),
) -> JSONResponse:
    body = await request.body()

    if settings.readai_webhook_signing_key:
        if not verify_readai_signature(body, x_read_signature, settings.readai_webhook_signing_key):
            logger.warning("Неверная подпись webhook Read AI")
            raise HTTPException(status_code=401, detail="Invalid webhook signature")

    try:
        payload = ReadAIWebhookPayload.model_validate_json(body)
    except Exception as exc:
        logger.exception("Невалидный JSON webhook")
        raise HTTPException(status_code=400, detail="Invalid JSON payload") from exc

    missing = settings.validate_runtime()
    if missing:
        raise HTTPException(
            status_code=503,
            detail=f"Missing configuration: {', '.join(missing)}",
        )

    if settings.is_vercel:
        await queue_meeting(payload)
        if (
            task_preview.enabled()
            and not payload.meeting_key.startswith("test-")
            and not task_preview.is_skipped(payload.title or "")
        ):
            await asyncio.to_thread(task_preview.trigger, payload.meeting_key)
        return JSONResponse({"status": "queued", "meeting_id": payload.meeting_key})

    background_tasks.add_task(queue_meeting, payload)
    return JSONResponse({"status": "queued", "meeting_id": payload.meeting_key})


@app.post("/internal/task-preview")
async def internal_task_preview(
    request: Request,
    x_internal_token: Optional[str] = Header(default=None, alias="X-Internal-Token"),
) -> JSONResponse:
    import hmac

    if not x_internal_token or not hmac.compare_digest(x_internal_token, task_preview.internal_token()):
        raise HTTPException(status_code=403, detail="Forbidden")
    body = await request.json()
    meeting_id = body.get("meeting_id", "")
    stage = body.get("stage", "digest")
    try:
        payload = load_pending_payload(meeting_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if stage == "transcript":
        return JSONResponse({"status": "sent", **await asyncio.to_thread(task_preview.send_transcript, payload)})

    if not task_preview.claim(payload.meeting_key):
        return JSONResponse({"status": "already_sent"})
    try:
        result = await asyncio.to_thread(task_preview.build_and_send, payload)
    except Exception:
        task_preview.release(payload.meeting_key)
        logger.exception("Дайджест упал для %s", payload.meeting_key)
        raise
    return JSONResponse({"status": "sent", **result})


@app.post("/webhooks/tasks-bot")
async def tasks_bot_webhook(
    update: Dict[str, Any],
    x_telegram_bot_api_secret_token: Optional[str] = Header(default=None, alias="X-Telegram-Bot-Api-Secret-Token"),
) -> JSONResponse:
    import hmac

    secret = task_preview.webhook_secret()
    if not x_telegram_bot_api_secret_token or not hmac.compare_digest(x_telegram_bot_api_secret_token, secret):
        raise HTTPException(status_code=403, detail="Forbidden")
    if update.get("callback_query"):
        await asyncio.to_thread(task_preview.handle_callback, update)
    elif update.get("my_chat_member"):
        await asyncio.to_thread(task_preview.remember_chat, update)
    return JSONResponse({"ok": True})


@app.post("/webhooks/telegram")
async def telegram_webhook(update: Dict[str, Any]) -> JSONResponse:
    if not settings.telegram_bot_token:
        raise HTTPException(status_code=503, detail="TELEGRAM_BOT_TOKEN is not configured")

    await handle_telegram_update(update)
    return JSONResponse({"status": "ok"})


@app.get("/cron/sync-projects")
async def cron_sync_projects(request: Request) -> JSONResponse:
    import os

    from app.services.notion_projects import sync_notion_projects_cache

    cron_secret = os.getenv("CRON_SECRET", "").strip()
    auth = request.headers.get("Authorization", "")
    is_vercel_cron = bool(request.headers.get("x-vercel-cron"))
    if cron_secret:
        if auth != f"Bearer {cron_secret}" and not is_vercel_cron:
            raise HTTPException(status_code=401, detail="Unauthorized")
    elif not is_vercel_cron and settings.is_vercel:
        raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        result = await sync_notion_projects_cache()
        return JSONResponse(result)
    except Exception as exc:
        logger.exception("Notion projects cron sync failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/test/process")
async def test_process(payload: ReadAIWebhookPayload) -> dict:
    missing = settings.validate_runtime()
    if missing:
        raise HTTPException(
            status_code=503,
            detail=f"Missing configuration: {', '.join(missing)}",
        )

    await process_meeting(payload)
    return {"status": "ok", "meeting_id": payload.meeting_key}
