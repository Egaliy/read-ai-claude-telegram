#!/usr/bin/env python3
"""Прогнать транскрипт через промпт саммари. Ничего не отправляет.

  .venv/bin/python scripts/try_prompt.py файл.txt [имя_промпта.txt]
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import task_preview  # noqa: E402

path = Path(sys.argv[1])
prompt = sys.argv[2] if len(sys.argv) > 2 else "meeting_digest.txt"
text = path.read_text(encoding="utf-8")
title = text.splitlines()[0].strip() or path.stem

if prompt == "meeting_digest.txt":
    d = task_preview.build_digest(title, text)
    print(f"ПРОЕКТ: {d['project']}\n\nСАММАРИ:\n{d['summary']}\n\nЗАДАЧИ:")
    for t in d["tasks"]:
        print(f"  {t.get('emoji','')} {t['title']}" + (f"  ⏰ {t['due']}" if t.get("due") else ""))
else:
    with task_preview._client().messages.stream(
        model=task_preview.settings.claude_model,
        max_tokens=16000,
        system=(ROOT / "prompts" / prompt).read_text(encoding="utf-8"),
        messages=[{"role": "user", "content": f"TRANSCRIPT ({title}):\n{text}"}],
    ) as s:
        msg = s.get_final_message()
    print("".join(b.text for b in msg.content if b.type == "text"))
