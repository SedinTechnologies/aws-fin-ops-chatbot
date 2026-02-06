"""Helpers for parsing structured LLM responses (title :: markdown :: json)."""

from __future__ import annotations

import json
import logging
import re
from html import unescape
from typing import List, Tuple

logger = logging.getLogger(__name__)


def _safe_parse_question_list(raw: str) -> List[dict] | None:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if isinstance(data, list) and all(isinstance(item, dict) for item in data):
        return data
    return None


def _extract_trailing_questions(markdown: str) -> Tuple[str, List[dict] | None]:
    text = markdown.rstrip()
    if not text.endswith("]"):
        return markdown, None

    start = text.rfind("[")
    if start == -1:
        return markdown, None

    candidate = text[start:]
    parsed = _safe_parse_question_list(candidate)
    if parsed is None:
        return markdown, None

    trimmed_markdown = text[:start].rstrip()
    return trimmed_markdown, parsed


def parse_structured_response(content: str) -> Tuple[str | None, str, List[dict]]:
    """Return (title, markdown, next_questions) from model output.

    The model usually emits `<title>::<markdown>::<json>` but sometimes omits the
    final delimiter. This routine is defensive so UI rendering stays clean.
    """
    safe_content = (content or "").strip()
    title: str | None = None
    markdown = safe_content
    questions: List[dict] | None = None

    parts = safe_content.split("::", 2)
    if len(parts) == 3:
        title_candidate, markdown_candidate, question_candidate = parts
        title = (title_candidate or "").strip() or None
        parsed_questions = _safe_parse_question_list(question_candidate.strip())
        if parsed_questions is not None:
            markdown = (markdown_candidate or "").strip()
            questions = parsed_questions
        else:
            markdown = f"{markdown_candidate}::{question_candidate}".strip()
    elif len(parts) == 2:
        title_candidate, markdown_candidate = parts
        title = (title_candidate or "").strip() or None
        markdown = (markdown_candidate or "").strip()

    if questions is None:
        markdown, questions = _extract_trailing_questions(markdown)

    if questions is None:
        logger.debug("Structured response missing next question JSON; returning markdown only")
        questions = []

    return title, _sanitize_markdown(markdown), questions


def _sanitize_markdown(markdown: str) -> str:
    """Convert simple HTML lists/headings into markdown bullets for Chainlit."""
    if "<" not in markdown or ">" not in markdown:
        return markdown

    cleaned = markdown
    replacements = [
        ("<ul>", "\n"),
        ("</ul>", "\n"),
        ("<ol>", "\n"),
        ("</ol>", "\n"),
        ("<br>", "\n"),
        ("<br/>", "\n"),
        ("<br />", "\n"),
        ("<strong>", "**"),
        ("</strong>", "**"),
        ("<b>", "**"),
        ("</b>", "**"),
        ("<em>", "*"),
        ("</em>", "*"),
        ("<i>", "*"),
        ("</i>", "*"),
        ("<p>", "\n\n"),
        ("</p>", "\n"),
    ]
    for needle, replacement in replacements:
        cleaned = cleaned.replace(needle, replacement)

    cleaned = cleaned.replace("<li>", "\n- ").replace("</li>", "")
    cleaned = cleaned.replace("&nbsp;", " ")
    cleaned = unescape(cleaned)
    cleaned = re.sub(r"<[^>]+>", "", cleaned)
    lines = [line.rstrip() for line in cleaned.splitlines()]
    return "\n".join(line for line in lines if line.strip()).strip()
