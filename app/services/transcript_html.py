"""Очищенный транскрипт → простая HTML-страница: имя и реплика, без графики.

Текст чёрный на белом, чтобы страницу было удобно читать и целиком копировать в ИИ.
"""
from __future__ import annotations

import html
import re
from typing import List, Tuple

# Пометки скрытого: ⟦финансы⟧ и т. п.
MARKER_RE = re.compile(r"⟦([^⟧]+)⟧")
LINE_RE = re.compile(r"^\s*\[?([^\]:]{1,60}?)\]?\s*:\s*(.+)$")


def _clean_name(raw: str) -> str:
    name = re.sub(r"\s*[-–—]\s*Speaker\s*\d+\s*$", "", raw.strip())
    name = re.sub(r"^Conference Room\s*\((.+)\)$", r"\1", name).strip()
    if name.upper() in {"UNKNOWN_SPEAKER", "UNIDENTIFIED SPEAKER"}:
        return "Неизвестный"
    return name or "Неизвестный"


def parse(cleaned: str) -> List[Tuple[str, str]]:
    """Строки «[Имя]: текст» → [(имя, реплика)], подряд идущие реплики склеиваются."""
    out: List[Tuple[str, str]] = []
    for raw_line in cleaned.splitlines():
        line = raw_line.strip().strip("`")
        if not line:
            continue
        m = LINE_RE.match(line)
        if not m:
            if out:  # продолжение предыдущей реплики
                out[-1] = (out[-1][0], out[-1][1] + " " + line)
            continue
        name, text = _clean_name(m.group(1)), m.group(2).strip()
        if out and out[-1][0] == name:
            out[-1] = (name, out[-1][1] + " " + text)
        else:
            out.append((name, text))
    return out


def _body(text: str) -> str:
    escaped = html.escape(text)
    return MARKER_RE.sub(lambda m: f"[скрыто: {m.group(1)}]", escaped)


def to_html(title: str, date: str, cleaned: str) -> str:
    messages = parse(cleaned)
    speakers = sorted({n for n, _ in messages})
    rows = "".join(
        f'<p><span class="n">{html.escape(name)}:</span> {_body(text)}</p>' for name, text in messages
    )
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@500;600&display=swap" rel="stylesheet">
<style>
  body {{ margin:0; background:#fff; color:#000;
          font-family:Inter, -apple-system, system-ui, sans-serif; font-weight:500;
          font-size:17px; line-height:1.55; }}
  .wrap {{ max-width:760px; margin:0 auto; padding:40px 20px 80px; }}
  h1 {{ font-size:19px; font-weight:600; margin:0 0 6px; }}
  .meta {{ font-size:15px; margin:0 0 32px; }}
  p {{ margin:0 0 18px; }}
  .n {{ font-weight:600; }}
</style></head>
<body><div class="wrap">
  <h1>{html.escape(title)}</h1>
  <p class="meta">{html.escape(date)} · {len(messages)} реплик · {html.escape(", ".join(speakers))}<br>
  Финансы, юридическое, коммерческие условия и личное скрыты.</p>
  {rows}
</div></body></html>"""
