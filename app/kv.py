from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Protocol


SUBSCRIBERS_KEY = "telegram:subscribers"
ALLOWED_CHATS_KEY = "telegram:allowed_chats"
PROCESSED_KEY = "processed:meetings"
NOTIFIED_KEY = "notified:meetings"
PENDING_MEETING_PREFIX = "pending:meeting:"
PROCESSING_LOCK_PREFIX = "processing:meeting:"
BRIEF_PREFIX = "brief:meeting:"
BRIEF_HTML_PREFIX = "brief_html:meeting:"
NOTION_URL_PREFIX = "notion:meeting:"
MEETING_PROJECT_PREFIX = "meeting:project:"
NOTION_PROJECTS_CACHE_KEY = "notion:projects:cache"
CHECKLIST_PREFIX = "checklist:"
PENDING_MEETING_TTL_SECONDS = 60 * 60 * 24 * 14
BRIEF_TTL_SECONDS = 60 * 60 * 24 * 30


class RedisLike(Protocol):
    def sadd(self, key: str, value: str) -> bool: ...
    def srem(self, key: str, value: str) -> None: ...
    def smembers(self, key: str) -> List[str]: ...
    def sismember(self, key: str, value: str) -> bool: ...
    def scard(self, key: str) -> int: ...
    def setex(self, key: str, ttl: int, value: str) -> None: ...
    def get(self, key: str) -> Optional[str]: ...


class UpstashRedisBackend:
    def __init__(self) -> None:
        from upstash_redis import Redis

        self.redis = Redis.from_env()

    def sadd(self, key: str, value: str) -> bool:
        return bool(self.redis.sadd(key, value))

    def srem(self, key: str, value: str) -> None:
        self.redis.srem(key, value)

    def smembers(self, key: str) -> List[str]:
        members = self.redis.smembers(key)
        return [str(item) for item in members]

    def sismember(self, key: str, value: str) -> bool:
        return bool(self.redis.sismember(key, value))

    def scard(self, key: str) -> int:
        return int(self.redis.scard(key) or 0)

    def setex(self, key: str, ttl: int, value: str) -> None:
        self.redis.set(key, value, ex=ttl)

    def get(self, key: str) -> Optional[str]:
        value = self.redis.get(key)
        return str(value) if value is not None else None


class RedisUrlBackend:
    def __init__(self, url: str) -> None:
        self.url = url

    def _client(self):
        import redis

        return redis.from_url(
            self.url,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
        )

    def sadd(self, key: str, value: str) -> bool:
        client = self._client()
        try:
            return bool(client.sadd(key, value))
        finally:
            client.close()

    def srem(self, key: str, value: str) -> None:
        client = self._client()
        try:
            client.srem(key, value)
        finally:
            client.close()

    def smembers(self, key: str) -> List[str]:
        client = self._client()
        try:
            return list(client.smembers(key))
        finally:
            client.close()

    def sismember(self, key: str, value: str) -> bool:
        client = self._client()
        try:
            return bool(client.sismember(key, value))
        finally:
            client.close()

    def scard(self, key: str) -> int:
        client = self._client()
        try:
            return int(client.scard(key) or 0)
        finally:
            client.close()

    def setex(self, key: str, ttl: int, value: str) -> None:
        client = self._client()
        try:
            client.setex(key, ttl, value)
        finally:
            client.close()

    def get(self, key: str) -> Optional[str]:
        client = self._client()
        try:
            return client.get(key)
        finally:
            client.close()


class SQLiteBackend:
    def __init__(self, db_path: str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS telegram_subscribers (
                    chat_id TEXT PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    subscribed_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS telegram_allowed_chats (
                    chat_id TEXT PRIMARY KEY,
                    added_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS processed_meetings (
                    meeting_id TEXT PRIMARY KEY,
                    processed_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS checklist_state (
                    checklist_id TEXT PRIMARY KEY,
                    data_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS pending_meetings (
                    meeting_id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS notified_meetings (
                    meeting_id TEXT PRIMARY KEY,
                    notified_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS meeting_briefs (
                    meeting_id TEXT PRIMARY KEY,
                    brief_markdown TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS meeting_brief_html (
                    meeting_id TEXT PRIMARY KEY,
                    brief_html TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS notion_projects_cache (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    payload_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS meeting_project_assignments (
                    meeting_id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS notion_pages (
                    meeting_id TEXT PRIMARY KEY,
                    page_url TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def add_subscriber(
        self,
        chat_id: str,
        username: Optional[str] = None,
        first_name: Optional[str] = None,
    ) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO telegram_subscribers
                (chat_id, username, first_name, subscribed_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    chat_id,
                    username,
                    first_name,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            return cursor.rowcount > 0

    def remove_subscriber(self, chat_id: str) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "DELETE FROM telegram_subscribers WHERE chat_id = ?",
                (chat_id,),
            )

    def list_subscribers(self) -> List[str]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT chat_id FROM telegram_subscribers ORDER BY subscribed_at"
            ).fetchall()
        return [row[0] for row in rows]

    def count_subscribers(self) -> int:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT COUNT(*) FROM telegram_subscribers").fetchone()
        return int(row[0]) if row else 0

    def add_allowed_chat(self, chat_id: str) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO telegram_allowed_chats (chat_id, added_at)
                VALUES (?, ?)
                """,
                (chat_id, datetime.now(timezone.utc).isoformat()),
            )
            return cursor.rowcount > 0

    def remove_allowed_chat(self, chat_id: str) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "DELETE FROM telegram_allowed_chats WHERE chat_id = ?",
                (chat_id,),
            )

    def list_allowed_chats(self) -> List[str]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT chat_id FROM telegram_allowed_chats ORDER BY added_at"
            ).fetchall()
        return [row[0] for row in rows]

    def is_allowed_chat(self, chat_id: str) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT 1 FROM telegram_allowed_chats WHERE chat_id = ?",
                (chat_id,),
            ).fetchone()
        return row is not None

    def count_allowed_chats(self) -> int:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT COUNT(*) FROM telegram_allowed_chats").fetchone()
        return int(row[0]) if row else 0

    def is_processed(self, meeting_id: str) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT 1 FROM processed_meetings WHERE meeting_id = ?",
                (meeting_id,),
            ).fetchone()
        return row is not None

    def mark_processed(self, meeting_id: str) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO processed_meetings (meeting_id, processed_at)
                VALUES (?, ?)
                """,
                (meeting_id, datetime.now(timezone.utc).isoformat()),
            )

    def save_checklist(self, checklist_id: str, data_json: str) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO checklist_state (checklist_id, data_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(checklist_id) DO UPDATE SET
                    data_json=excluded.data_json,
                    updated_at=excluded.updated_at
                """,
                (checklist_id, data_json, datetime.now(timezone.utc).isoformat()),
            )

    def load_checklist(self, checklist_id: str) -> Optional[str]:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT data_json FROM checklist_state WHERE checklist_id = ?",
                (checklist_id,),
            ).fetchone()
        return row[0] if row else None

    def save_notion_projects_cache(self, payload_json: str) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO notion_projects_cache (id, payload_json, updated_at)
                VALUES (1, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    updated_at = excluded.updated_at
                """,
                (payload_json, datetime.now(timezone.utc).isoformat()),
            )

    def load_notion_projects_cache(self) -> Optional[str]:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT payload_json FROM notion_projects_cache WHERE id = 1",
            ).fetchone()
        return row[0] if row else None

    def save_pending_meeting(self, meeting_id: str, payload_json: str) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO pending_meetings (meeting_id, payload_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(meeting_id) DO UPDATE SET
                    payload_json=excluded.payload_json,
                    updated_at=excluded.updated_at
                """,
                (meeting_id, payload_json, datetime.now(timezone.utc).isoformat()),
            )

    def load_pending_meeting(self, meeting_id: str) -> Optional[str]:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT payload_json FROM pending_meetings WHERE meeting_id = ?",
                (meeting_id,),
            ).fetchone()
        return row[0] if row else None

    def list_pending_meeting_ids(self) -> List[str]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT meeting_id FROM pending_meetings ORDER BY updated_at"
            ).fetchall()
        return [row[0] for row in rows]

    def is_notified(self, meeting_id: str) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT 1 FROM notified_meetings WHERE meeting_id = ?",
                (meeting_id,),
            ).fetchone()
        return row is not None

    def mark_notified(self, meeting_id: str) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO notified_meetings (meeting_id, notified_at)
                VALUES (?, ?)
                """,
                (meeting_id, datetime.now(timezone.utc).isoformat()),
            )

    def save_brief(self, meeting_id: str, brief_markdown: str) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO meeting_briefs (meeting_id, brief_markdown, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(meeting_id) DO UPDATE SET
                    brief_markdown=excluded.brief_markdown,
                    updated_at=excluded.updated_at
                """,
                (meeting_id, brief_markdown, datetime.now(timezone.utc).isoformat()),
            )

    def load_brief(self, meeting_id: str) -> Optional[str]:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT brief_markdown FROM meeting_briefs WHERE meeting_id = ?",
                (meeting_id,),
            ).fetchone()
        return row[0] if row else None

    def delete_brief(self, meeting_id: str) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM meeting_briefs WHERE meeting_id = ?", (meeting_id,))

    def save_brief_html(self, meeting_id: str, brief_html: str) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO meeting_brief_html (meeting_id, brief_html, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(meeting_id) DO UPDATE SET
                    brief_html=excluded.brief_html,
                    updated_at=excluded.updated_at
                """,
                (meeting_id, brief_html, datetime.now(timezone.utc).isoformat()),
            )

    def load_brief_html(self, meeting_id: str) -> Optional[str]:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT brief_html FROM meeting_brief_html WHERE meeting_id = ?",
                (meeting_id,),
            ).fetchone()
        return row[0] if row else None

    def delete_brief_html(self, meeting_id: str) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM meeting_brief_html WHERE meeting_id = ?", (meeting_id,))

    def save_notion_url(self, meeting_id: str, page_url: str) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO notion_pages (meeting_id, page_url, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(meeting_id) DO UPDATE SET
                    page_url=excluded.page_url,
                    updated_at=excluded.updated_at
                """,
                (meeting_id, page_url, datetime.now(timezone.utc).isoformat()),
            )

    def load_notion_url(self, meeting_id: str) -> Optional[str]:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT page_url FROM notion_pages WHERE meeting_id = ?",
                (meeting_id,),
            ).fetchone()
        return row[0] if row else None

    def delete_notion_url(self, meeting_id: str) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM notion_pages WHERE meeting_id = ?", (meeting_id,))

    def save_meeting_project_assignment(self, meeting_id: str, payload_json: str) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO meeting_project_assignments (meeting_id, payload_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(meeting_id) DO UPDATE SET
                    payload_json=excluded.payload_json,
                    updated_at=excluded.updated_at
                """,
                (meeting_id, payload_json, datetime.now(timezone.utc).isoformat()),
            )

    def load_meeting_project_assignment(self, meeting_id: str) -> Optional[str]:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT payload_json FROM meeting_project_assignments WHERE meeting_id = ?",
                (meeting_id,),
            ).fetchone()
        return row[0] if row else None

    def delete_meeting_project_assignment(self, meeting_id: str) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "DELETE FROM meeting_project_assignments WHERE meeting_id = ?",
                (meeting_id,),
            )


def _use_upstash() -> bool:
    return bool(os.getenv("UPSTASH_REDIS_REST_URL") and os.getenv("UPSTASH_REDIS_REST_TOKEN"))


def _use_redis_url() -> bool:
    return bool(os.getenv("REDIS_URL"))


def _use_remote_storage() -> bool:
    return _use_upstash() or _use_redis_url()


def _create_redis_backend() -> RedisLike:
    if _use_upstash():
        return UpstashRedisBackend()
    url = os.getenv("REDIS_URL")
    if url:
        return RedisUrlBackend(url)
    raise RuntimeError("Redis backend requested but REDIS_URL is not configured")


def _resolve_db_path(db_path: str) -> str:
    if os.getenv("VERCEL") and not _use_remote_storage():
        return "/tmp/readai-claude.db"
    return db_path


class SubscriberStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = _resolve_db_path(db_path)
        self.redis: Optional[RedisLike] = _create_redis_backend() if _use_remote_storage() else None
        self.sqlite = None if self.redis else SQLiteBackend(self.db_path)

    def add(
        self,
        chat_id: str,
        username: Optional[str] = None,
        first_name: Optional[str] = None,
    ) -> bool:
        if self.redis:
            return self.redis.sadd(SUBSCRIBERS_KEY, chat_id)
        assert self.sqlite is not None
        return self.sqlite.add_subscriber(chat_id, username, first_name)

    def remove(self, chat_id: str) -> None:
        if self.redis:
            self.redis.srem(SUBSCRIBERS_KEY, chat_id)
            return
        assert self.sqlite is not None
        self.sqlite.remove_subscriber(chat_id)

    def list_chat_ids(self) -> List[str]:
        if self.redis:
            return self.redis.smembers(SUBSCRIBERS_KEY)
        assert self.sqlite is not None
        return self.sqlite.list_subscribers()

    def count(self) -> int:
        if self.redis:
            return self.redis.scard(SUBSCRIBERS_KEY)
        assert self.sqlite is not None
        return self.sqlite.count_subscribers()


class AllowedChatsStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = _resolve_db_path(db_path)
        self.redis: Optional[RedisLike] = _create_redis_backend() if _use_remote_storage() else None
        self.sqlite = None if self.redis else SQLiteBackend(self.db_path)

    def add(self, chat_id: str) -> bool:
        if self.redis:
            return self.redis.sadd(ALLOWED_CHATS_KEY, chat_id)
        assert self.sqlite is not None
        return self.sqlite.add_allowed_chat(chat_id)

    def remove(self, chat_id: str) -> None:
        if self.redis:
            self.redis.srem(ALLOWED_CHATS_KEY, chat_id)
            return
        assert self.sqlite is not None
        self.sqlite.remove_allowed_chat(chat_id)

    def contains(self, chat_id: str) -> bool:
        if self.redis:
            return self.redis.sismember(ALLOWED_CHATS_KEY, chat_id)
        assert self.sqlite is not None
        return self.sqlite.is_allowed_chat(chat_id)

    def list_chat_ids(self) -> List[str]:
        if self.redis:
            return self.redis.smembers(ALLOWED_CHATS_KEY)
        assert self.sqlite is not None
        return self.sqlite.list_allowed_chats()

    def count(self) -> int:
        if self.redis:
            return self.redis.scard(ALLOWED_CHATS_KEY)
        assert self.sqlite is not None
        return self.sqlite.count_allowed_chats()


class ProcessedMeetingsStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = _resolve_db_path(db_path)
        self.redis: Optional[RedisLike] = _create_redis_backend() if _use_remote_storage() else None
        self.sqlite = None if self.redis else SQLiteBackend(self.db_path)

    def is_processed(self, meeting_id: str) -> bool:
        if self.redis:
            return self.redis.sismember(PROCESSED_KEY, meeting_id)
        assert self.sqlite is not None
        return self.sqlite.is_processed(meeting_id)

    def mark_processed(self, meeting_id: str) -> None:
        if self.redis:
            self.redis.sadd(PROCESSED_KEY, meeting_id)
            return
        assert self.sqlite is not None
        self.sqlite.mark_processed(meeting_id)

    def unmark_processed(self, meeting_id: str) -> None:
        if self.redis:
            self.redis.srem(PROCESSED_KEY, meeting_id)
            return
        assert self.sqlite is not None
        with sqlite3.connect(self.sqlite.db_path) as conn:
            conn.execute(
                "DELETE FROM processed_meetings WHERE meeting_id = ?",
                (meeting_id,),
            )


class BriefStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = _resolve_db_path(db_path)
        self.redis: Optional[RedisLike] = _create_redis_backend() if _use_remote_storage() else None
        self.sqlite = None if self.redis else SQLiteBackend(self.db_path)

    def save(self, meeting_id: str, brief_markdown: str) -> None:
        if self.redis:
            self.redis.setex(
                f"{BRIEF_PREFIX}{meeting_id}",
                BRIEF_TTL_SECONDS,
                brief_markdown,
            )
            return
        assert self.sqlite is not None
        self.sqlite.save_brief(meeting_id, brief_markdown)

    def load(self, meeting_id: str) -> Optional[str]:
        if self.redis:
            return self.redis.get(f"{BRIEF_PREFIX}{meeting_id}")
        assert self.sqlite is not None
        return self.sqlite.load_brief(meeting_id)

    def delete(self, meeting_id: str) -> None:
        if self.redis:
            if isinstance(self.redis, RedisUrlBackend):
                client = self.redis._client()
                try:
                    client.delete(f"{BRIEF_PREFIX}{meeting_id}")
                finally:
                    client.close()
            elif isinstance(self.redis, UpstashRedisBackend):
                self.redis.redis.delete(f"{BRIEF_PREFIX}{meeting_id}")
            return
        assert self.sqlite is not None
        self.sqlite.delete_brief(meeting_id)


class BriefHtmlStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = _resolve_db_path(db_path)
        self.redis: Optional[RedisLike] = _create_redis_backend() if _use_remote_storage() else None
        self.sqlite = None if self.redis else SQLiteBackend(self.db_path)

    def save(self, meeting_id: str, brief_html: str) -> None:
        if self.redis:
            self.redis.setex(
                f"{BRIEF_HTML_PREFIX}{meeting_id}",
                BRIEF_TTL_SECONDS,
                brief_html,
            )
            return
        assert self.sqlite is not None
        self.sqlite.save_brief_html(meeting_id, brief_html)

    def load(self, meeting_id: str) -> Optional[str]:
        if self.redis:
            return self.redis.get(f"{BRIEF_HTML_PREFIX}{meeting_id}")
        assert self.sqlite is not None
        return self.sqlite.load_brief_html(meeting_id)

    def delete(self, meeting_id: str) -> None:
        if self.redis:
            if isinstance(self.redis, RedisUrlBackend):
                client = self.redis._client()
                try:
                    client.delete(f"{BRIEF_HTML_PREFIX}{meeting_id}")
                finally:
                    client.close()
            elif isinstance(self.redis, UpstashRedisBackend):
                self.redis.redis.delete(f"{BRIEF_HTML_PREFIX}{meeting_id}")
            return
        assert self.sqlite is not None
        self.sqlite.delete_brief_html(meeting_id)


class NotionPageStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = _resolve_db_path(db_path)
        self.redis: Optional[RedisLike] = _create_redis_backend() if _use_remote_storage() else None
        self.sqlite = None if self.redis else SQLiteBackend(self.db_path)

    def save(self, meeting_id: str, page_url: str) -> None:
        if self.redis:
            self.redis.setex(
                f"{NOTION_URL_PREFIX}{meeting_id}",
                BRIEF_TTL_SECONDS,
                page_url,
            )
            return
        assert self.sqlite is not None
        self.sqlite.save_notion_url(meeting_id, page_url)

    def load(self, meeting_id: str) -> Optional[str]:
        if self.redis:
            return self.redis.get(f"{NOTION_URL_PREFIX}{meeting_id}")
        assert self.sqlite is not None
        return self.sqlite.load_notion_url(meeting_id)

    def delete(self, meeting_id: str) -> None:
        if self.redis:
            if isinstance(self.redis, RedisUrlBackend):
                client = self.redis._client()
                try:
                    client.delete(f"{NOTION_URL_PREFIX}{meeting_id}")
                finally:
                    client.close()
            elif isinstance(self.redis, UpstashRedisBackend):
                self.redis.redis.delete(f"{NOTION_URL_PREFIX}{meeting_id}")
            return
        assert self.sqlite is not None
        self.sqlite.delete_notion_url(meeting_id)


class NotifiedMeetingsStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = _resolve_db_path(db_path)
        self.redis: Optional[RedisLike] = _create_redis_backend() if _use_remote_storage() else None
        self.sqlite = None if self.redis else SQLiteBackend(self.db_path)

    def is_notified(self, meeting_id: str) -> bool:
        if self.redis:
            return self.redis.sismember(NOTIFIED_KEY, meeting_id)
        assert self.sqlite is not None
        return self.sqlite.is_notified(meeting_id)

    def mark_notified(self, meeting_id: str) -> None:
        if self.redis:
            self.redis.sadd(NOTIFIED_KEY, meeting_id)
            return
        assert self.sqlite is not None
        self.sqlite.mark_notified(meeting_id)


class PendingMeetingStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = _resolve_db_path(db_path)
        self.redis: Optional[RedisLike] = _create_redis_backend() if _use_remote_storage() else None
        self.sqlite = None if self.redis else SQLiteBackend(self.db_path)

    def save(self, meeting_id: str, payload_json: str) -> None:
        if self.redis:
            self.redis.setex(
                f"{PENDING_MEETING_PREFIX}{meeting_id}",
                PENDING_MEETING_TTL_SECONDS,
                payload_json,
            )
            return
        assert self.sqlite is not None
        self.sqlite.save_pending_meeting(meeting_id, payload_json)

    def load(self, meeting_id: str) -> Optional[str]:
        if self.redis:
            return self.redis.get(f"{PENDING_MEETING_PREFIX}{meeting_id}")
        assert self.sqlite is not None
        return self.sqlite.load_pending_meeting(meeting_id)

    def list_ids(self) -> List[str]:
        if self.redis:
            if isinstance(self.redis, RedisUrlBackend):
                redis_client = self.redis._client()
                try:
                    keys = sorted(redis_client.scan_iter(f"{PENDING_MEETING_PREFIX}*"))
                    return [key.removeprefix(PENDING_MEETING_PREFIX) for key in keys]
                finally:
                    redis_client.close()
            if isinstance(self.redis, UpstashRedisBackend):
                keys = self.redis.redis.keys(f"{PENDING_MEETING_PREFIX}*")
                return sorted(
                    str(key).removeprefix(PENDING_MEETING_PREFIX) for key in keys
                )
            return []
        assert self.sqlite is not None
        return self.sqlite.list_pending_meeting_ids()

    def try_acquire_processing_lock(self, meeting_id: str, ttl: int = 600) -> bool:
        if not self.redis:
            return True
        client = self.redis
        if isinstance(client, RedisUrlBackend):
            redis_client = client._client()
            try:
                return bool(
                    redis_client.set(
                        f"{PROCESSING_LOCK_PREFIX}{meeting_id}",
                        "1",
                        nx=True,
                        ex=ttl,
                    )
                )
            finally:
                redis_client.close()
        if isinstance(client, UpstashRedisBackend):
            return bool(
                client.redis.set(
                    f"{PROCESSING_LOCK_PREFIX}{meeting_id}",
                    "1",
                    nx=True,
                    ex=ttl,
                )
            )
        return True

    def release_processing_lock(self, meeting_id: str) -> None:
        if not self.redis:
            return
        key = f"{PROCESSING_LOCK_PREFIX}{meeting_id}"
        if isinstance(self.redis, RedisUrlBackend):
            redis_client = self.redis._client()
            try:
                redis_client.delete(key)
            finally:
                redis_client.close()
            return
        if isinstance(self.redis, UpstashRedisBackend):
            self.redis.redis.delete(key)


CHECKLIST_TTL_SECONDS = 60 * 60 * 24 * 30


class ChecklistStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = _resolve_db_path(db_path)
        self.redis: Optional[RedisLike] = _create_redis_backend() if _use_remote_storage() else None
        self.sqlite = None if self.redis else SQLiteBackend(self.db_path)

    def save(self, checklist_id: str, data: dict) -> None:
        payload = json.dumps(data, ensure_ascii=False)
        key = f"{CHECKLIST_PREFIX}{checklist_id}"
        if self.redis:
            self.redis.setex(key, CHECKLIST_TTL_SECONDS, payload)
            return
        assert self.sqlite is not None
        self.sqlite.save_checklist(checklist_id, payload)

    def load(self, checklist_id: str) -> Optional[dict]:
        key = f"{CHECKLIST_PREFIX}{checklist_id}"
        if self.redis:
            raw = self.redis.get(key)
        else:
            assert self.sqlite is not None
            raw = self.sqlite.load_checklist(checklist_id)
        if not raw:
            return None
        return json.loads(raw)

    def update_message_id(self, checklist_id: str, message_id: int) -> None:
        state = self.load(checklist_id)
        if not state:
            return
        state["message_id"] = message_id
        self.save(checklist_id, state)


PROJECTS_CACHE_TTL_SECONDS = 60 * 60 * 26


class NotionProjectsCacheStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = _resolve_db_path(db_path)
        self.redis: Optional[RedisLike] = _create_redis_backend() if _use_remote_storage() else None
        self.sqlite = None if self.redis else SQLiteBackend(self.db_path)

    def save(self, data: dict) -> None:
        payload = json.dumps(data, ensure_ascii=False)
        if self.redis:
            self.redis.setex(
                NOTION_PROJECTS_CACHE_KEY,
                PROJECTS_CACHE_TTL_SECONDS,
                payload,
            )
            return
        assert self.sqlite is not None
        self.sqlite.save_notion_projects_cache(payload)

    def load(self) -> Optional[dict]:
        if self.redis:
            raw = self.redis.get(NOTION_PROJECTS_CACHE_KEY)
        else:
            assert self.sqlite is not None
            raw = self.sqlite.load_notion_projects_cache()
        if not raw:
            return None
        return json.loads(raw)


class MeetingProjectStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = _resolve_db_path(db_path)
        self.redis: Optional[RedisLike] = _create_redis_backend() if _use_remote_storage() else None
        self.sqlite = None if self.redis else SQLiteBackend(self.db_path)

    def save(self, meeting_id: str, data: dict) -> None:
        payload = json.dumps(data, ensure_ascii=False)
        key = f"{MEETING_PROJECT_PREFIX}{meeting_id}"
        if self.redis:
            self.redis.setex(key, PENDING_MEETING_TTL_SECONDS, payload)
            return
        assert self.sqlite is not None
        self.sqlite.save_meeting_project_assignment(meeting_id, payload)

    def load(self, meeting_id: str) -> Optional[dict]:
        key = f"{MEETING_PROJECT_PREFIX}{meeting_id}"
        if self.redis:
            raw = self.redis.get(key)
        else:
            assert self.sqlite is not None
            raw = self.sqlite.load_meeting_project_assignment(meeting_id)
        if not raw:
            return None
        return json.loads(raw)

    def delete(self, meeting_id: str) -> None:
        key = f"{MEETING_PROJECT_PREFIX}{meeting_id}"
        if self.redis:
            if isinstance(self.redis, RedisUrlBackend):
                client = self.redis._client()
                try:
                    client.delete(key)
                finally:
                    client.close()
            elif isinstance(self.redis, UpstashRedisBackend):
                self.redis.redis.delete(key)
            return
        assert self.sqlite is not None
        self.sqlite.delete_meeting_project_assignment(meeting_id)
