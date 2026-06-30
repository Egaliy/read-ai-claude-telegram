from __future__ import annotations

import html
import re
from typing import List


_CSS = """
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
       line-height: 1.5; color: #111; max-width: 720px; margin: 0 auto; padding: 20px 16px 40px; }
h1 { font-size: 1.35rem; margin: 0 0 12px; line-height: 1.3; }
h2 { font-size: 1.05rem; margin: 28px 0 12px; padding-bottom: 6px; border-bottom: 1px solid #e5e5e5; }
.meta { color: #555; margin: 6px 0; }
.tldr { background: #f4f6f8; border-left: 4px solid #2563eb; padding: 10px 12px; margin: 16px 0; border-radius: 4px; }
.vision { background: #faf5ff; border-left: 4px solid #9333ea; padding: 10px 12px; margin: 16px 0; border-radius: 4px; }
.task { border: 1px solid #e8e8e8; border-radius: 8px; padding: 12px 14px; margin: 12px 0; background: #fafafa; }
.task-title { font-weight: 600; margin-bottom: 8px; }
.task-line { margin: 4px 0; }
.task-line.label { color: #444; font-weight: 500; }
.quote { margin: 6px 0 6px 12px; padding-left: 10px; border-left: 3px solid #ccc; color: #333; font-style: italic; }
.fallback { color: #b45309; font-weight: 600; margin-top: 24px; }
"""


def markdown_brief_to_html(markdown_text: str) -> str:
    lines = markdown_text.strip().splitlines()
    body_parts: List[str] = []
    task_lines: List[str] = []

    def flush_task() -> None:
        nonlocal task_lines
        if not task_lines:
            return
        body_parts.append(_render_task(task_lines))
        task_lines = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            flush_task()
            continue

        if stripped.startswith("# "):
            flush_task()
            body_parts.append(f"<h1>{_inline(stripped[2:])}</h1>")
            continue

        if stripped.startswith("## "):
            flush_task()
            body_parts.append(f"<h2>{_inline(stripped[3:])}</h2>")
            continue

        if stripped.startswith("Participants:"):
            flush_task()
            body_parts.append(f'<p class="meta"><b>Participants:</b> {_inline(stripped[13:].strip())}</p>')
            continue

        if stripped.startswith("TL;DR:"):
            flush_task()
            body_parts.append(f'<div class="tldr"><b>TL;DR:</b> {_inline(stripped[6:].strip())}</div>')
            continue

        if stripped.startswith("Vision / Tone:"):
            flush_task()
            body_parts.append(
                f'<div class="vision"><b>Vision / Tone:</b> {_inline(stripped[14:].strip())}</div>'
            )
            continue

        if stripped.startswith("⚠️ Сбой self-check"):
            flush_task()
            body_parts.append(f'<p class="fallback">{_inline(stripped)}</p>')
            continue

        if stripped.startswith("[") and "]" in stripped[:30]:
            flush_task()
            task_lines = [stripped]
            continue

        if _is_bold_task_title(stripped):
            flush_task()
            task_lines = [stripped]
            continue

        if stripped == "---":
            flush_task()
            continue

        if task_lines and (stripped.startswith("→") or stripped.startswith('"')):
            task_lines.append(stripped)
            continue

        flush_task()
        body_parts.append(f"<p>{_inline(stripped)}</p>")

    flush_task()

    body = "\n".join(body_parts)
    return (
        "<!DOCTYPE html>\n"
        '<html lang="ru">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<style>{_CSS}</style>\n"
        "</head>\n<body>\n"
        f"{body}\n"
        "</body>\n</html>"
    )


def _render_task(lines: List[str]) -> str:
    title_line = lines[0].strip()
    if title_line.startswith("**") and title_line.endswith("**"):
        title = _inline(title_line.strip("*").strip())
    else:
        title = _inline(title_line)
    chunks: List[str] = [f'<div class="task-title">{title}</div>']

    for line in lines[1:]:
        if line.startswith('"') or (line.startswith("→ Source:") and '"' in line):
            quote_text = line
            if line.startswith("→ Source:"):
                quote_text = line.removeprefix("→ Source:").strip()
            chunks.append(f'<div class="quote">{_inline(quote_text)}</div>')
            continue

        if line.startswith("→"):
            label, _, rest = line.partition(":")
            chunks.append(
                f'<div class="task-line">'
                f'<span class="label">{_inline(label)}:</span> {_inline(rest.strip())}'
                f"</div>"
            )
            continue

        chunks.append(f'<div class="task-line">{_inline(line)}</div>')

    return f'<div class="task">{"".join(chunks)}</div><!-- task -->'


def _inline(text: str) -> str:
    escaped = html.escape(text, quote=False)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"\*(.+?)\*", r"<em>\1</em>", escaped)
    return escaped


def _is_bold_task_title(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("**") and stripped.endswith("**") and stripped.count("**") >= 2
