#!/usr/bin/env python3
"""Синхронизировать список проектов из Notion Project Base в Redis."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from app.services.notion_projects import get_notion_projects, sync_notion_projects_cache


async def main() -> None:
    result = await sync_notion_projects_cache()
    projects = await get_notion_projects(force_refresh=False)
    print(result)
    print(f"projects in cache: {len(projects)}")
    for project in sorted(projects, key=lambda item: item.name.casefold()):
        print(f"- {project.name}: {project.url}")


if __name__ == "__main__":
    asyncio.run(main())
