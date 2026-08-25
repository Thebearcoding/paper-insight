from __future__ import annotations

import html
import json
import re
from typing import Any

from prompt import ZOTERO_NOTE_AND_TAG_PROMPT


TAG_GROUPS = {"主题", "任务", "方法", "数据集", "应用", "状态"}
MAX_TAGS = 12
MAX_NOTE_CHARS = 8_000
PAPER_INSIGHT_NOTE_MARKER = "paper-insight-ai-note:v1"
PAPER_INSIGHT_NOTE_TAG = "来源/Paper Insight"


def _extract_json_object(raw_text: str) -> dict[str, Any]:
    text = raw_text.strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise
        parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise ValueError("Zotero enrichment response is not a JSON object")
    return parsed


def normalize_suggested_tags(raw_tags: object, existing_tags: list[str] | None = None) -> list[dict]:
    existing = {str(tag).strip().casefold() for tag in existing_tags or [] if str(tag).strip()}
    normalized: list[dict] = []
    seen: set[str] = set()
    for raw_tag in raw_tags if isinstance(raw_tags, list) else []:
        if isinstance(raw_tag, dict):
            group = str(raw_tag.get("group") or "").strip()
            value = str(raw_tag.get("value") or "").strip()
        else:
            text = str(raw_tag or "").strip()
            group, separator, value = text.partition("/")
            if not separator:
                continue
            group = group.strip()
            value = value.strip()
        value = re.sub(r"\s+", " ", value).strip(" /,，;；")
        if group not in TAG_GROUPS or not value:
            continue
        tag = f"{group}/{value}"[:100]
        folded = tag.casefold()
        if folded in existing or folded in seen:
            continue
        seen.add(folded)
        normalized.append({"group": group, "value": value[:90], "tag": tag})
        if len(normalized) >= MAX_TAGS:
            break
    return normalized


def normalize_zotero_enrichment(
    raw_result: dict[str, Any],
    *,
    existing_tags: list[str] | None = None,
) -> dict[str, Any]:
    note_markdown = str(raw_result.get("note_markdown") or "").strip()[:MAX_NOTE_CHARS]
    if not note_markdown:
        raise ValueError("Claude 没有生成 Zotero 笔记")
    tags = normalize_suggested_tags(raw_result.get("tags"), existing_tags)
    return {
        "note_markdown": note_markdown,
        "tags": tags,
        "writeback": {"status": "pending", "note_item_key": None},
    }


async def generate_zotero_enrichment(
    llm,
    item: dict[str, Any],
    report: str,
) -> dict[str, Any]:
    existing_tags = [str(tag) for tag in item.get("tags") or [] if str(tag).strip()]
    prompt = "\n".join(
        [
            f"论文标题：{item.get('title') or '材料中未说明'}",
            f"发表信息：{item.get('publication_title') or '材料中未说明'}",
            f"已有标签：{json.dumps(existing_tags, ensure_ascii=False)}",
            "",
            "深度阅读报告：",
            report,
        ]
    )
    raw_response = await llm.chat(
        [
            {"role": "system", "content": ZOTERO_NOTE_AND_TAG_PROMPT},
            {"role": "user", "content": prompt},
        ],
        temperature=0.1,
        max_tokens=2500,
        _usage_context="zotero_note_and_tags",
    )
    parsed = _extract_json_object(raw_response or "")
    return normalize_zotero_enrichment(parsed, existing_tags=existing_tags)


def markdown_to_zotero_note_html(markdown: str, title: str) -> str:
    blocks: list[str] = [
        f'<div data-paper-insight-note="{PAPER_INSIGHT_NOTE_MARKER}">',
        f"<h1>{html.escape(title or 'AI 精读笔记')}</h1>",
    ]
    in_list = False
    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if not line:
            if in_list:
                blocks.append("</ul>")
                in_list = False
            continue
        heading = re.match(r"^(#{1,4})\s+(.+)$", line)
        bullet = re.match(r"^[-*+]\s+(.+)$", line)
        if heading:
            if in_list:
                blocks.append("</ul>")
                in_list = False
            level = min(len(heading.group(1)) + 1, 4)
            blocks.append(f"<h{level}>{html.escape(heading.group(2))}</h{level}>")
        elif bullet:
            if not in_list:
                blocks.append("<ul>")
                in_list = True
            blocks.append(f"<li>{html.escape(bullet.group(1))}</li>")
        else:
            if in_list:
                blocks.append("</ul>")
                in_list = False
            blocks.append(f"<p>{html.escape(line)}</p>")
    if in_list:
        blocks.append("</ul>")
    blocks.append("<p><em>由 Paper Insight AI 分析生成；请结合原论文核对。</em></p>")
    blocks.append("</div>")
    return "".join(blocks)
