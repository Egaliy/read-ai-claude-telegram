"""Очищенный транскрипт → HTML-страница в виде чата: аватар, имя, реплика."""
from __future__ import annotations

import hashlib
import html
import re
from typing import List, Tuple

# Пометки скрытого: ⟦финансы⟧ и т. п.
MARKER_RE = re.compile(r"⟦([^⟧]+)⟧")
LINE_RE = re.compile(r"^\s*\[?([^\]:]{1,60}?)\]?\s*:\s*(.+)$")

PALETTE = [
    ("#2f5bea", "#dce4ff"), ("#c2410c", "#ffe6d5"), ("#0f766e", "#d4f3ef"),
    ("#7c3aed", "#ece1ff"), ("#b91c1c", "#ffe0e0"), ("#0369a1", "#dbeefe"),
    ("#4d7c0f", "#e7f5cf"), ("#a21caf", "#fbe0fb"),
]


def _clean_name(raw: str) -> str:
    name = re.sub(r"\s*[-–—]\s*Speaker\s*\d+\s*$", "", raw.strip())
    name = re.sub(r"^Conference Room\s*\((.+)\)$", r"\1", name).strip()
    if name.upper() in {"UNKNOWN_SPEAKER", "UNIDENTIFIED SPEAKER"}:
        return "Неизвестный"
    return name or "Неизвестный"


def _initials(name: str) -> str:
    parts = [p for p in re.split(r"[\s._-]+", name) if p]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][:1] + parts[1][:1]).upper()


def _colors(name: str) -> Tuple[str, str]:
    idx = int(hashlib.sha1(name.encode("utf-8")).hexdigest(), 16) % len(PALETTE)
    return PALETTE[idx]


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
            out[-1] = (name, out[-1][1] + "\n" + text)
        else:
            out.append((name, text))
    return out


def _body(text: str) -> str:
    escaped = html.escape(text)
    escaped = MARKER_RE.sub(lambda m: f'<span class="hidden-part" title="скрыто: {m.group(1)}">скрыто: {m.group(1)}</span>', escaped)
    return escaped.replace("\n", "<br>")


def to_html(title: str, date: str, cleaned: str) -> str:
    messages = parse(cleaned)
    speakers = sorted({n for n, _ in messages})
    rows = []
    prev = None
    for name, text in messages:
        ink, bg = _colors(name)
        same = name == prev
        avatar = (
            '<div class="av-spacer"></div>'
            if same
            else f'<div class="av" style="background:{bg};color:{ink}">{html.escape(_initials(name))}</div>'
        )
        header = "" if same else f'<div class="who" style="color:{ink}">{html.escape(name)}</div>'
        rows.append(f'<div class="msg{" cont" if same else ""}">{avatar}<div class="bubble">{header}<div class="text">{_body(text)}</div></div></div>')
        prev = name

    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
  :root {{ --bg:#f5f4f1; --panel:#fff; --ink:#1b1b19; --mute:#79766f; --line:#e6e3dd; }}
  @media (prefers-color-scheme: dark) {{ :root {{ --bg:#141413; --panel:#1e1e1c; --ink:#ecebe7; --mute:#9a978f; --line:#2f2e2b; }} }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--ink);
         font:15px/1.55 -apple-system, "SF Pro Text", Inter, system-ui, sans-serif; }}
  .wrap {{ max-width:720px; margin:0 auto; padding:28px 16px 64px; }}
  header {{ margin-bottom:22px; }}
  h1 {{ font-size:19px; margin:0 0 6px; letter-spacing:-.01em; }}
  .meta {{ color:var(--mute); font-size:13px; }}
  .note {{ margin-top:12px; padding:9px 12px; border:1px solid var(--line); border-radius:9px;
           background:var(--panel); color:var(--mute); font-size:12.5px; }}
  .msg {{ display:flex; gap:10px; margin-top:14px; align-items:flex-start; }}
  .msg.cont {{ margin-top:3px; }}
  .av, .av-spacer {{ width:30px; height:30px; flex:0 0 30px; border-radius:50%; }}
  .av {{ display:flex; align-items:center; justify-content:center; font-size:11.5px; font-weight:650; letter-spacing:.02em; }}
  .bubble {{ background:var(--panel); border:1px solid var(--line); border-radius:13px;
             padding:8px 12px; max-width:calc(100% - 40px); }}
  .who {{ font-size:12.5px; font-weight:650; margin-bottom:2px; }}
  .text {{ white-space:normal; overflow-wrap:anywhere; }}
  .hidden-part {{ background:repeating-linear-gradient(45deg,var(--line),var(--line) 6px,transparent 6px,transparent 12px);
                  border:1px solid var(--line); border-radius:5px; padding:0 6px;
                  color:var(--mute); font-size:12px; white-space:nowrap; }}
</style></head>
<body><div class="wrap">
  <header>
    <h1>{html.escape(title)}</h1>
    <div class="meta">{html.escape(date)} · {len(messages)} реплик · {html.escape(", ".join(speakers))}</div>
    <div class="note">Финансы, юридическое, коммерческие условия и личное скрыты.</div>
  </header>
  {"".join(rows)}
</div></body></html>"""
