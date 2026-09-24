"""Документ по звонку: бриф (markdown от модели) + очищенный транскрипт → один HTML-файл."""
from __future__ import annotations

import html
import re
from typing import List, Tuple

from app.services.transcript_html import parse as parse_transcript

LABEL_RE = re.compile(r"^(Статус|Описание|Цитата|Причина|Зависимость|Кто|Срок|Комментарий команды):\s*(.*)$")

# Цвет кружка по статусу. Текст рядом обязателен — цветом одним статус не передаём.
STATUS_COLORS = {
    "к выполнению": "#c0392b",
    "обсуждается": "#c98a12",
    "требует подтверждения": "#c98a12",
    "принято клиентом": "#2e7d4f",
    "отклонено": "#6d6a62",
    "отменено": "#6d6a62",
}


def _status_color(text: str) -> str:
    low = text.lower()
    for key, color in STATUS_COLORS.items():
        if low.startswith(key):
            return color
    return "#6d6a62"


def _inline(text: str) -> str:
    escaped = html.escape(text)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"`([^`]+)`", r"\1", escaped)
    return re.sub(r"⟦([^⟧]+)⟧", r'<span class="hidden">скрыто: \1</span>', escaped)


def _quote(value: str) -> str:
    """«реплика» — Имя (1:03) → blockquote с подписью под цитатой."""
    m = re.match(r"^[«\"'“](.+)[»\"'”]\s*[—–-]\s*(.+)$", value.strip(), re.S)
    if not m:
        return f"<blockquote><p>{_inline(value)}</p></blockquote>"
    return f"<blockquote><p>{_inline(m.group(1))}</p><cite>{_inline(m.group(2))}</cite></blockquote>"


def _brief_html(markdown: str) -> Tuple[str, str, str]:
    """Возвращает (заголовок, мета-строка, html разделов)."""
    title = ""
    meta = ""
    parts: List[str] = []
    card_open = section_open = list_open = False

    def close_list() -> None:
        nonlocal list_open
        if list_open:
            parts.append("</ul>")
            list_open = False

    def close_card() -> None:
        nonlocal card_open
        close_list()
        if card_open:
            parts.append("</article>")
            card_open = False

    def close_section() -> None:
        nonlocal section_open
        close_card()
        if section_open:
            parts.append("</section>")
            section_open = False

    for raw in markdown.splitlines():
        line = raw.strip().strip("`")
        if not line or re.fullmatch(r"[-*_—]{3,}", line):  # разделители рисует вёрстка
            continue
        if line.startswith("# "):
            title = line[2:].strip()
            continue
        if line.startswith("## "):
            close_section()
            name = line[3:].strip()
            kind = "summary" if name.lower().startswith(("summary", "итог")) else (
                "feedback" if "фидбек" in name.lower() else "block"
            )
            parts.append(f'<section class="{kind}"><h2>{_inline(name)}</h2>')
            section_open = True
            continue
        if line.startswith("### "):
            close_card()
            parts.append(f'<article class="card"><h3>{_inline(line[4:].strip())}</h3>')
            card_open = True
            continue
        if line.startswith(("- ", "* ")):
            if not list_open:
                parts.append("<ul>")
                list_open = True
            parts.append(f"<li>{_inline(line[2:])}</li>")
            continue
        close_list()
        m = LABEL_RE.match(line)
        if m and m.group(1) == "Статус":
            value = m.group(2).strip().strip("`").strip()
            parts.append(
                f'<p class="status"><span class="dot" style="background:{_status_color(value)}"></span>'
                f"<span>{_inline(value)}</span></p>"
            )
            continue
        if m and m.group(1) == "Цитата":
            parts.append(_quote(m.group(2)))
            continue
        if m and m.group(1) == "Описание":
            parts.append(f"<p>{_inline(m.group(2))}</p>")
            continue
        if m:
            parts.append(f'<p><span class="label">{html.escape(m.group(1))}:</span> {_inline(m.group(2))}</p>')
            continue
        if not section_open and not title:
            title = line
            continue
        if not section_open:
            meta = line if not meta else meta + " · " + line
            continue
        parts.append(f"<p>{_inline(line)}</p>")

    close_section()
    return title, meta, "".join(parts)


def _transcript_html(cleaned: str) -> str:
    return "".join(
        f'<p class="turn"><span class="who">{html.escape(name)}</span><br>{_inline(text)}</p>'
        for name, text in parse_transcript(cleaned)
    )


def build(title: str, date: str, participants: str, brief_markdown: str, cleaned_transcript: str) -> str:
    doc_title, meta, brief = _brief_html(brief_markdown)
    fallback_meta = " · ".join(p for p in [f"Созвон: {date}" if date else "", participants] if p)
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(doc_title or title)}</title>
<style>
  body {{ margin:0; background:#fff; color:#15150f;
          font-family:-apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
          font-size:16.5px; line-height:1.6; }}
  .wrap {{ max-width:760px; margin:0 auto; padding:48px 20px 96px; }}
  h1 {{ font-size:27px; line-height:1.22; margin:0 0 8px; letter-spacing:-.015em; }}
  .meta {{ color:#6d6a62; font-size:14.5px; margin:0; }}
  section {{ border-top:1px solid #eae7e1; margin-top:40px; padding-top:28px; }}
  section:first-of-type {{ border-top:none; }}
  h2 {{ font-size:13px; text-transform:uppercase; letter-spacing:.09em; color:#6d6a62;
        margin:0 0 18px; font-weight:600; }}
  h3 {{ font-size:17.5px; margin:0 0 10px; }}
  p {{ margin:0 0 10px; }}
  ul {{ margin:0 0 10px; padding-left:22px; }}
  li {{ margin:0 0 6px; }}
  .card {{ border:1px solid #e7e4dd; border-radius:12px; padding:18px 20px; margin:0 0 14px; }}
  .card > :last-child {{ margin-bottom:0; }}
  .status {{ display:flex; align-items:center; gap:8px; font-size:14.5px; color:#403f38; margin-bottom:10px; }}
  .dot {{ width:9px; height:9px; border-radius:50%; flex:0 0 9px; }}
  .label {{ color:#6d6a62; font-weight:600; }}
  blockquote {{ margin:12px 0; padding:2px 0 2px 16px; border-left:2px solid #d9d5cc; }}
  blockquote p {{ font-family:Georgia, "Times New Roman", serif; font-style:italic; color:#33322c; margin:0; }}
  cite {{ display:block; margin-top:6px; font-style:normal; font-size:13.5px; color:#6d6a62; }}
  .hidden {{ color:#6d6a62; background:#f2f0ec; border-radius:5px; padding:0 6px; font-size:14px; }}
  .summary, .feedback {{ background:#f7f6f2; border:1px solid #e7e4dd; border-radius:14px;
                         padding:22px 24px; border-top:1px solid #e7e4dd; }}
  .summary h2, .feedback h2 {{ margin-bottom:12px; }}
  .transcript .turn {{ margin:0 0 16px; }}
  .who {{ font-weight:600; }}
  @media (max-width:640px) {{ .wrap {{ padding:32px 16px 72px; }} h1 {{ font-size:23px; }} }}
</style></head>
<body><div class="wrap">
  <h1>{html.escape(doc_title or title)}</h1>
  <p class="meta">{html.escape(meta or fallback_meta)}</p>
  {brief}
  <section class="transcript">
    <h2>Транскрипт созвона</h2>
    <p class="meta">Финансовое, юридическое, коммерческое и личное вырезано.</p>
    {_transcript_html(cleaned_transcript)}
  </section>
</div></body></html>"""
