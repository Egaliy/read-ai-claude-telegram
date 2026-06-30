from __future__ import annotations

import html as html_module
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app.config import settings
from app.models import ReadAIWebhookPayload

logger = logging.getLogger(__name__)

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
MAX_RICH_TEXT = 2000
MAX_BLOCKS_PER_REQUEST = 100
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def notion_enabled() -> bool:
    return bool(settings.notion_token.strip() and settings.notion_parent_page_id.strip())


def _headers() -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.notion_token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _strip_html(fragment: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", fragment, flags=re.I)
    text = _HTML_TAG_RE.sub("", text)
    return html_module.unescape(text).strip()


def _rich_text(content: str) -> List[Dict[str, Any]]:
    chunks: List[Dict[str, Any]] = []
    text = content or " "
    for index in range(0, len(text), MAX_RICH_TEXT):
        chunks.append({"type": "text", "text": {"content": text[index : index + MAX_RICH_TEXT]}})
    return chunks


def _paragraph(text: str) -> Dict[str, Any]:
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": _rich_text(text)}}


def _heading(level: int, text: str) -> Dict[str, Any]:
    block_type = f"heading_{level}"
    return {
        "object": "block",
        "type": block_type,
        block_type: {"rich_text": _rich_text(text), "is_toggleable": False},
    }


def _quote(text: str) -> Dict[str, Any]:
    return {"object": "block", "type": "quote", "quote": {"rich_text": _rich_text(text)}}


def _callout(text: str, emoji: str = "📌") -> Dict[str, Any]:
    return {
        "object": "block",
        "type": "callout",
        "callout": {
            "rich_text": _rich_text(text),
            "icon": {"type": "emoji", "emoji": emoji},
        },
    }


def _todo(text: str, *, checked: bool = False) -> Dict[str, Any]:
    return {
        "object": "block",
        "type": "to_do",
        "to_do": {"rich_text": _rich_text(text), "checked": checked},
    }


def _task_detail(text: str) -> Dict[str, Any]:
    if text.lower().startswith("source:") or text.startswith('"'):
        return _quote(text)
    return _paragraph(text)


def _parse_task_blocks(task_html: str) -> List[Dict[str, Any]]:
    title_match = re.search(r'<div class="task-title">(.*?)</div>', task_html, re.S | re.I)
    if not title_match:
        return []

    blocks: List[Dict[str, Any]] = [_todo(_strip_html(title_match.group(1)))]

    for quote_match in re.finditer(r'<div class="quote">(.*?)</div>', task_html, re.S | re.I):
        blocks.append(_quote(_strip_html(quote_match.group(1))))

    for line_match in re.finditer(r'<div class="task-line"(?:[^>]*)>(.*?)</div>', task_html, re.S | re.I):
        detail = _strip_html(line_match.group(1))
        if detail:
            blocks.append(_task_detail(detail))

    return blocks


def _divider() -> Dict[str, Any]:
    return {"object": "block", "type": "divider", "divider": {}}


def parse_page_title_from_html(html_doc: str, payload: ReadAIWebhookPayload) -> str:
    match = re.search(r"<h1>(.*?)</h1>", html_doc, re.S | re.I)
    if match:
        title = _strip_html(match.group(1))
        if title:
            return title
    title = payload.title or "Командный звонок"
    date_part = ""
    if payload.start_time and "T" in payload.start_time:
        date_part = payload.start_time.split("T", 1)[0]
    if date_part:
        return f"Brief — {title} · {date_part}"
    return f"Brief — {title}"


def html_brief_to_notion_blocks(html_doc: str) -> List[Dict[str, Any]]:
    body_match = re.search(r"<body>(.*)</body>", html_doc, re.S | re.I)
    body = body_match.group(1) if body_match else html_doc
    blocks: List[Dict[str, Any]] = []

    token_re = re.compile(
        r'<h2>(.*?)</h2>'
        r'|<div class="tldr">(.*?)</div>'
        r'|<div class="vision">(.*?)</div>'
        r'|<p class="meta">(.*?)</p>'
        r'|<p class="fallback">(.*?)</p>'
        r'|<div class="task">(.*?)</div><!-- task -->'
        r'|<p>(.*?)</p>',
        re.S | re.I,
    )

    for match in token_re.finditer(body):
        if match.group(1) is not None:
            blocks.append(_heading(2, _strip_html(match.group(1))))
        elif match.group(2) is not None:
            blocks.append(_callout(_strip_html(match.group(2)), "💡"))
        elif match.group(3) is not None:
            blocks.append(_callout(_strip_html(match.group(3)), "🎨"))
        elif match.group(4) is not None:
            blocks.append(_paragraph(_strip_html(match.group(4))))
        elif match.group(5) is not None:
            blocks.append(_callout(_strip_html(match.group(5)), "⚠️"))
        elif match.group(6) is not None:
            blocks.extend(_parse_task_blocks(match.group(6)))
        elif match.group(7) is not None:
            text = _strip_html(match.group(7))
            if text:
                blocks.append(_paragraph(text))

    return blocks


async def publish_brief_html_to_notion(
    payload: ReadAIWebhookPayload,
    brief_html: str,
) -> Optional[str]:
    if not notion_enabled():
        return None

    page_title = parse_page_title_from_html(brief_html, payload)
    blocks = html_brief_to_notion_blocks(brief_html)

    meta_lines = [f"Meeting ID: {payload.meeting_key}"]
    if payload.start_time:
        meta_lines.append(f"Start: {payload.start_time}")
    if payload.end_time:
        meta_lines.append(f"End: {payload.end_time}")
    blocks.insert(0, _paragraph(" · ".join(meta_lines)))

    page_id, page_url = await _create_page(page_title, blocks)
    logger.info("Notion page created from HTML: %s (%s)", page_title, page_id)
    return page_url


async def _create_page(title: str, blocks: List[Dict[str, Any]]) -> Tuple[str, str]:
    parent_id = settings.notion_parent_page_id.strip()
    first_batch = blocks[:MAX_BLOCKS_PER_REQUEST]
    rest = blocks[MAX_BLOCKS_PER_REQUEST:]

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            f"{NOTION_API}/pages",
            headers=_headers(),
            json={
                "parent": {"type": "page_id", "page_id": _normalize_id(parent_id)},
                "properties": {
                    "title": {"title": [{"type": "text", "text": {"content": title[:2000]}}]},
                },
                "children": first_batch,
            },
        )
        if response.status_code >= 400:
            raise RuntimeError(f"Notion create page failed: {response.text[:500]}")
        data = response.json()
        page_id = data["id"]
        page_url = data.get("url") or _page_url(page_id)

        while rest:
            batch = rest[:MAX_BLOCKS_PER_REQUEST]
            rest = rest[MAX_BLOCKS_PER_REQUEST:]
            append_response = await client.patch(
                f"{NOTION_API}/blocks/{page_id}/children",
                headers=_headers(),
                json={"children": batch},
            )
            if append_response.status_code >= 400:
                raise RuntimeError(
                    f"Notion append blocks failed: {append_response.text[:500]}"
                )

    return page_id, page_url


async def append_link_to_page(page_id: str, link_title: str, page_url: str) -> None:
    """Добавить ссылку на бриф/расшифровку в тело указанной страницы Notion."""
    if not notion_enabled() or not page_id.strip():
        return

    target_id = _normalize_id(page_id.strip())
    block = {
        "object": "block",
        "type": "bulleted_list_item",
        "bulleted_list_item": {
            "rich_text": [
                {
                    "type": "text",
                    "text": {
                        "content": link_title[:2000],
                        "link": {"url": page_url},
                    },
                }
            ]
        },
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.patch(
            f"{NOTION_API}/blocks/{target_id}/children",
            headers=_headers(),
            json={"children": [block]},
        )
        if response.status_code >= 400:
            logger.warning(
                "Notion link append failed for %s: %s",
                target_id,
                response.text[:300],
            )


async def append_brief_to_hub_index(page_title: str, page_url: str) -> None:
    """Добавить ссылку на бриф в тело hub-страницы."""
    await append_link_to_page(settings.notion_parent_page_id.strip(), page_title, page_url)


def _normalize_id(page_id: str) -> str:
    cleaned = page_id.strip()
    if re.fullmatch(r"[0-9a-fA-F]{32}", cleaned):
        return (
            f"{cleaned[0:8]}-{cleaned[8:12]}-{cleaned[12:16]}-"
            f"{cleaned[16:20]}-{cleaned[20:32]}"
        )
    return cleaned


def _page_url(page_id: str) -> str:
    slug = page_id.replace("-", "")
    return f"https://www.notion.so/{slug}"
