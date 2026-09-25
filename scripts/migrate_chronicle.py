#!/usr/bin/env python3
"""Перенос старой общей базы задач и летописей внутрь страниц проектов.

  set -a; . ./.env; set +a; .venv/bin/python scripts/migrate_chronicle.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import chronicle as c  # noqa: E402

OLD_TASKS_DB = "chronicle:tasks_db"
OLD_LOG = "chronicle:log:"
OLD_HUB = "🤖 База знаний агента"

r = c._redis()
old_db = r.get(OLD_TASKS_DB)
if not old_db:
    raise SystemExit("Старой базы задач нет — переносить нечего")

rows, cursor = [], None
while True:
    body = {"page_size": 100}
    if cursor:
        body["start_cursor"] = cursor
    res = c._api("POST", f"databases/{old_db}/query", body)
    rows += res["results"]
    cursor = res.get("next_cursor") if res.get("has_more") else None
    if not cursor:
        break
print("задач в старой базе:", len(rows))

moved = 0
for row in rows:
    p = row["properties"]
    name = c._plain(p["Проект"]["rich_text"]) or "Без проекта"
    project = c.find_or_create_project(name)
    pages = c.project_pages(project)
    c.create_task(
        pages["tasks"],
        {
            "title": c._plain(p["Задача"]["title"]),
            "status": (p["Статус"].get("select") or {}).get("name", "Новая"),
            "assignee": c._plain(p["Исполнитель"]["rich_text"]),
            "due": c._plain(p["Срок"]["rich_text"]),
            "note": c._plain(p["Заметка"]["rich_text"]),
        },
        c._plain(p["Источник"]["rich_text"]),
        (p["Обновлено"].get("date") or {}).get("start") or "2026-09-24",
    )
    c._api("PATCH", f"pages/{row['id']}", {"archived": True})
    moved += 1
print("перенесено задач:", moved)

# Старые страницы «Летопись звонков» и служебная строка больше не нужны.
for key in r.scan_iter(OLD_LOG + "*"):
    page_id = r.get(key)
    if page_id:
        try:
            c._api("PATCH", f"pages/{page_id}", {"archived": True})
        except Exception as exc:
            print("не убралась летопись", page_id, exc)
    r.delete(key)

hub_key = c.PROJECT_PAGE_KEY + c._slug(OLD_HUB)
hub = r.get(hub_key)
if hub:
    c._api("PATCH", f"pages/{hub}", {"archived": True})
    r.delete(hub_key)
r.delete(OLD_TASKS_DB)
print("старая структура убрана в архив")
