#!/usr/bin/env python3
"""Создать hub-страницу Read AI Briefs в Notion и вывести NOTION_PARENT_PAGE_ID."""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

import httpx

from app.config import settings
from app.services.notion import NOTION_API, _headers, _normalize_id


async def create_hub(under_page_id: str, title: str) -> str:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{NOTION_API}/pages",
            headers=_headers(),
            json={
                "parent": {"type": "page_id", "page_id": _normalize_id(under_page_id)},
                "properties": {
                    "title": {"title": [{"type": "text", "text": {"content": title}}]},
                },
                "children": [
                    {
                        "object": "block",
                        "type": "paragraph",
                        "paragraph": {
                            "rich_text": [
                                {
                                    "type": "text",
                                    "text": {
                                        "content": (
                                            "Сюда автоматически попадают брифы по созвонам "
                                            "после нажатия «Получить бриф» в Telegram."
                                        )
                                    },
                                }
                            ]
                        },
                    }
                ],
            },
        )
        if response.status_code >= 400:
            raise RuntimeError(response.text[:500])
        data = response.json()
        return data["id"]


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--under",
        required=True,
        help="ID любой страницы Notion, к которой у интеграции есть доступ",
    )
    parser.add_argument("--title", default="Read AI Briefs")
    args = parser.parse_args()

    if not settings.notion_token.strip():
        raise SystemExit("Задайте NOTION_TOKEN в .env")

    page_id = await create_hub(args.under, args.title)
    print("Hub создан.")
    print(f"NOTION_PARENT_PAGE_ID={page_id}")
    print("Добавьте эту строку в .env и на Vercel.")


if __name__ == "__main__":
    asyncio.run(main())
