from __future__ import annotations

import html


def escape_html(text: str) -> str:
    return html.escape(text or "", quote=False)
