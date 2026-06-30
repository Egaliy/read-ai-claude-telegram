from __future__ import annotations

import os
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    anthropic_api_key: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_chat_ids: str = ""
    telegram_admin_ids: str = ""
    readai_webhook_signing_key: str = ""
    claude_model: str = "claude-sonnet-4-20250514"
    port: int = 8000
    prompt_file: str = "prompts/tasks_extraction.txt"
    meeting_title_filter: str = ""
    database_path: str = "data/processed.db"
    telegram_polling: bool = True
    telegram_webhook_url: str = ""
    telegram_typewriter: bool = True
    telegram_business_connection_id: str = ""
    upstash_redis_rest_url: str = ""
    upstash_redis_rest_token: str = ""
    redis_url: str = ""
    notion_token: str = ""
    notion_parent_page_id: str = ""
    notion_projects_database_id: str = ""
    notion_general_page_id: str = ""
    vercel_stable_domain: str = "read-ai-claude-telegram.vercel.app"

    @property
    def notion_enabled(self) -> bool:
        return bool(self.notion_token.strip() and self.notion_parent_page_id.strip())

    @property
    def telegram_typewriter_enabled(self) -> bool:
        if self.is_vercel:
            return False
        return self.telegram_typewriter

    @property
    def is_vercel(self) -> bool:
        return bool(os.getenv("VERCEL"))

    @property
    def prompt_path(self):
        from pathlib import Path

        return Path(self.prompt_file)

    @property
    def use_polling(self) -> bool:
        if self.is_vercel:
            return False
        return self.telegram_polling

    @property
    def resolved_public_base_url(self) -> str:
        if self.telegram_webhook_url.strip():
            return (
                self.telegram_webhook_url.strip()
                .rstrip("/")
                .removesuffix("/webhooks/telegram")
            )
        if self.is_vercel:
            domain = (self.vercel_stable_domain or os.getenv("VERCEL_STABLE_DOMAIN") or "").strip()
            if domain:
                return f"https://{domain.rstrip('/')}"
        return ""

    @property
    def resolved_telegram_webhook_url(self) -> str:
        if self.telegram_webhook_url.strip():
            return self.telegram_webhook_url.rstrip("/")

        base = self.resolved_public_base_url
        if base:
            return f"{base}/webhooks/telegram"

        vercel_url = os.getenv("VERCEL_PROJECT_PRODUCTION_URL") or os.getenv("VERCEL_URL")
        if vercel_url:
            if vercel_url.startswith("http"):
                base = vercel_url.rstrip("/")
            else:
                base = f"https://{vercel_url.rstrip('/')}"
            return f"{base}/webhooks/telegram"
        return ""

    @property
    def fixed_chat_ids(self) -> List[str]:
        ids: List[str] = []
        if self.telegram_chat_id.strip():
            ids.append(self.telegram_chat_id.strip())
        if self.telegram_chat_ids.strip():
            ids.extend(
                part.strip()
                for part in self.telegram_chat_ids.split(",")
                if part.strip()
            )
        seen = set()
        unique: List[str] = []
        for chat_id in ids:
            if chat_id not in seen:
                seen.add(chat_id)
                unique.append(chat_id)
        return unique

    @property
    def admin_chat_ids(self) -> List[str]:
        ids: List[str] = []
        if self.telegram_admin_ids.strip():
            ids.extend(
                part.strip()
                for part in self.telegram_admin_ids.split(",")
                if part.strip()
            )
        seen = set()
        unique: List[str] = []
        for chat_id in ids:
            if chat_id not in seen:
                seen.add(chat_id)
                unique.append(chat_id)
        return unique

    @property
    def seed_allowed_chat_ids(self) -> List[str]:
        seen = set()
        unique: List[str] = []
        for chat_id in self.admin_chat_ids + self.fixed_chat_ids:
            if chat_id not in seen:
                seen.add(chat_id)
                unique.append(chat_id)
        return unique

    @property
    def storage_backend(self) -> str:
        if os.getenv("UPSTASH_REDIS_REST_URL") and os.getenv("UPSTASH_REDIS_REST_TOKEN"):
            return "upstash"
        if os.getenv("REDIS_URL"):
            return "redis"
        return "sqlite"

    def validate_runtime(self) -> List[str]:
        missing: List[str] = []
        if not self.anthropic_api_key:
            missing.append("ANTHROPIC_API_KEY")
        if not self.telegram_bot_token:
            missing.append("TELEGRAM_BOT_TOKEN")
        if self.is_vercel and self.storage_backend == "sqlite":
            pass
        return missing


settings = Settings()
