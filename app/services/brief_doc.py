"""Документ по звонку: бриф (markdown от модели) + очищенный транскрипт → один HTML-файл."""
from __future__ import annotations

import html
import re
from typing import List, Tuple

from app.services.transcript_html import parse as parse_transcript

LABEL_RE = re.compile(r"^(Что|Почему|Статус|Кто|Срок|Когда|Причина):\s*(.+)$")


def _inline(text: str) -> str:
    escaped = html.escape(text)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    return re.sub(r"⟦([^⟧]+)⟧", r'<span class="hidden">скрыто: \1</span>', escaped)


def _brief_html(markdown: str) -> Tuple[str, str]:
    """Возвращает (заголовок документа, html разделов)."""
    title = ""
    parts: List[str] = []
    card_open = False
    section_open = False

    def close_card() -> None:
        nonlocal card_open
        if card_open:
            parts.append("</div>")
            card_open = False

    def close_section() -> None:
        nonlocal section_open
        close_card()
        if section_open:
            parts.append("</section>")
            section_open = False

    for raw in markdown.splitlines():
        line = raw.strip().strip("`")
        if not line:
            continue
        if line.startswith("# "):
            title = line[2:].strip()
            continue
        if line.startswith("## "):
            close_section()
            name = line[3:].strip()
            kind = "summary" if name.lower().startswith("итог") else "block"
            parts.append(f'<section class="{kind}"><h2>{_inline(name)}</h2>')
            section_open = True
            continue
        if line.startswith("### "):
            close_card()
            parts.append(f'<div class="card"><h3>{_inline(line[4:].strip())}</h3>')
            card_open = True
            continue
        if line.startswith(("- ", "* ")):
            parts.append(f"<p>{_inline(line[2:])}</p>")
            continue
        m = LABEL_RE.match(line)
        if m:
            parts.append(f'<p><span class="label">{html.escape(m.group(1))}</span> {_inline(m.group(2))}</p>')
        else:
            parts.append(f"<p>{_inline(line)}</p>")
    close_section()
    return title, "".join(parts)


def _transcript_html(cleaned: str) -> str:
    rows = []
    for name, text in parse_transcript(cleaned):
        rows.append(f'<p class="turn"><span class="who">{html.escape(name)}</span> {_inline(text)}</p>')
    return "".join(rows)


def build(title: str, date: str, participants: str, brief_markdown: str, cleaned_transcript: str) -> str:
    doc_title, brief = _brief_html(brief_markdown)
    meta = " · ".join(p for p in [date, participants] if p)
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(doc_title or title)}</title>
<style>
  body {{ margin:0; background:#fff; color:#15150f;
          font-family:-apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
          font-size:16.5px; line-height:1.6; }}
  .wrap {{ max-width:760px; margin:0 auto; padding:48px 20px 96px; }}
  h1 {{ font-size:26px; line-height:1.25; margin:0 0 8px; letter-spacing:-.015em; }}
  .meta {{ color:#6d6a62; font-size:14.5px; margin:0 0 40px; }}
  h2 {{ font-size:13px; text-transform:uppercase; letter-spacing:.08em; color:#6d6a62;
        margin:44px 0 16px; font-weight:600; }}
  h3 {{ font-size:17px; margin:0 0 8px; }}
  p {{ margin:0 0 10px; }}
  .card {{ border:1px solid #e7e4dd; border-radius:12px; padding:16px 18px; margin:0 0 12px; }}
  .card p:last-child {{ margin-bottom:0; }}
  .label {{ color:#6d6a62; font-weight:600; }}
  .hidden {{ color:#6d6a62; background:#f2f0ec; border-radius:5px; padding:0 6px; font-size:14px; }}
  .summary {{ background:#f7f6f2; border:1px solid #e7e4dd; border-radius:14px;
              padding:20px 22px; margin-top:44px; }}
  .summary h2 {{ margin:0 0 10px; }}
  .transcript {{ margin-top:56px; border-top:1px solid #e7e4dd; padding-top:12px; }}
  .turn {{ margin:0 0 14px; }}
  .who {{ font-weight:600; }}
  @media (max-width:640px) {{ .wrap {{ padding:32px 16px 72px; }} h1 {{ font-size:22px; }} }}
</style></head>
<body><div class="wrap">
  <h1>{html.escape(doc_title or title)}</h1>
  <p class="meta">{html.escape(meta)}</p>
  {brief}
  <section class="transcript">
    <h2>Транскрипт звонка</h2>
    <p class="meta">Финансовое, юридическое, коммерческое и личное вырезано.</p>
    {_transcript_html(cleaned_transcript)}
  </section>
</div></body></html>"""
