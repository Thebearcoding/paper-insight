from __future__ import annotations

import html
import hashlib
import json
import re
from typing import Any

from llm import LLMOutputTruncatedError, is_glm_proxy_config
from markdown_utils import normalize_zotero_report
from prompt import ZOTERO_NOTE_AND_TAG_PROMPT


TAG_GROUPS = {"主题", "任务", "方法", "数据集", "应用", "状态"}
MAX_TAGS = 12
MAX_NOTE_CHARS = 8_000
PAPER_INSIGHT_NOTE_MARKER = "paper-insight-ai-note:v1"
PAPER_INSIGHT_NOTE_TAG = "来源/Paper Insight"
COMPACT_QUERY_KEY_TOKEN_PATTERN = re.compile(
    r"(?<![$\\{A-Za-z0-9_])([AN])_q([AN])_?k(?![A-Za-z0-9_])"
)
PROTECTED_NOTE_SEGMENT_PATTERN = re.compile(
    r"(`{3,}|~{3,})[^\n]*\n[\s\S]*?(?:\n[ \t]*\1[ \t]*(?=\n|$)|$)"
    r"|(`+)[^\n]*?\2"
    r"|!?\[[^\]\n]*\]\([^\n]*?\)|https?://[^\s<>]+"
    r"|(?<!\\)\$\$[\s\S]*?(?:\$\$|$)"
    r"|(?<![\\$])\$(?!\$)(?:\\.|[^$\n])*(?:\$|$)",
)


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


def normalize_note_math_notation(note_markdown: str) -> str:
    """Restore the omitted `_` in compact normal/anomaly query-key symbols."""
    fragments: list[str] = []
    cursor = 0
    for match in PROTECTED_NOTE_SEGMENT_PATTERN.finditer(note_markdown):
        fragments.append(COMPACT_QUERY_KEY_TOKEN_PATTERN.sub(
            lambda token: f"{token.group(1)}_q{token.group(2)}_k", note_markdown[cursor:match.start()]
        ))
        fragments.append(match.group())
        cursor = match.end()
    fragments.append(COMPACT_QUERY_KEY_TOKEN_PATTERN.sub(
        lambda token: f"{token.group(1)}_q{token.group(2)}_k", note_markdown[cursor:]
    ))
    return "".join(fragments)


def render_zotero_note_inline_text(text: str) -> str:
    """Escape note text while preserving query/key subscripts in Zotero HTML."""
    if PROTECTED_NOTE_SEGMENT_PATTERN.search(text):
        fragments: list[str] = []
        cursor = 0
        for match in PROTECTED_NOTE_SEGMENT_PATTERN.finditer(text):
            fragments.append(render_zotero_note_inline_text(text[cursor:match.start()]))
            protected = match.group()
            if protected.startswith("$") and not protected.startswith("$$") and protected.endswith("$") and len(protected) > 2:
                # Zotero's note-editor schema recognizes span.math with $...$.
                fragments.append(f'<span class="math">{html.escape(protected)}</span>')
            else:
                fragments.append(html.escape(protected))
            cursor = match.end()
        fragments.append(render_zotero_note_inline_text(text[cursor:]))
        return "".join(fragments)
    fragments: list[str] = []
    cursor = 0
    for match in COMPACT_QUERY_KEY_TOKEN_PATTERN.finditer(text):
        fragments.append(html.escape(text[cursor:match.start()]))
        query_class, key_class = match.groups()
        fragments.append(
            f"{html.escape(query_class)}<sub>q</sub>"
            f"{html.escape(key_class)}<sub>k</sub>"
        )
        cursor = match.end()
    fragments.append(html.escape(text[cursor:]))
    return "".join(fragments)


def normalize_zotero_enrichment(
    raw_result: dict[str, Any],
    *,
    existing_tags: list[str] | None = None,
    previous_enrichment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    note_markdown = normalize_note_math_notation(
        str(raw_result.get("note_markdown") or "").strip()
    )
    if not note_markdown:
        raise ValueError("模型没有生成 Zotero 笔记")
    if len(note_markdown) > MAX_NOTE_CHARS:
        raise ValueError("笔记过长，请压缩后返回完整 JSON，不得截断公式或句子")
    tags = normalize_suggested_tags(raw_result.get("tags"), existing_tags)
    previous_writeback = (previous_enrichment or {}).get("writeback") or {}
    return {
        "note_markdown": note_markdown,
        "tags": tags,
        "writeback": {
            "status": "pending",
            "note_item_key": previous_writeback.get("note_item_key"),
        },
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
    chat_options: dict[str, Any] = {
        "temperature": 0.1,
        "max_tokens": 4096,
    }
    try:
        public_config = llm.public_config()
    except (AttributeError, RuntimeError):
        public_config = {}
    if is_glm_proxy_config(public_config):
        chat_options.update(
            {
                "max_tokens": 8192,
                "thinking": {"type": "disabled"},
                "output_config": {"effort": "low"},
            }
        )
    last_error: Exception | None = None
    for attempt in range(1, 3):
        retry_instruction = ""
        if attempt > 1:
            retry_instruction = (
                "\n\n上一次输出未形成完整 JSON。此次务必压缩笔记到 900 个汉字以内，"
                "保证 JSON 完整闭合后再结束输出。"
            )
        try:
            raw_response = await llm.chat(
                [
                    {"role": "system", "content": ZOTERO_NOTE_AND_TAG_PROMPT},
                    {"role": "user", "content": prompt + retry_instruction},
                ],
                _usage_context=(
                    "zotero_note_and_tags"
                    if attempt == 1
                    else "zotero_note_and_tags_retry"
                ),
                **chat_options,
            )
            parsed = _extract_json_object(raw_response or "")
            enrichment = normalize_zotero_enrichment(
                parsed,
                existing_tags=existing_tags,
                previous_enrichment=item.get("analysis_enrichment"),
            )
            enrichment["source_report_hash"] = report_fingerprint(report)
            enrichment["report_status"] = "current"
            return enrichment
        except (json.JSONDecodeError, ValueError, LLMOutputTruncatedError) as exc:
            last_error = exc
    raise ValueError("模型未返回完整的 Zotero 笔记与标签 JSON") from last_error


def report_fingerprint(report: str) -> str:
    return hashlib.sha256(normalize_zotero_report(report).encode()).hexdigest()


def enrichment_matches_report(enrichment: dict[str, Any], report: str) -> bool:
    if enrichment.get("report_status") in {"stale", "pending"}:
        return False
    source_hash = enrichment.get("source_report_hash")
    # Legacy notes predate provenance hashes and remain compatible.
    return not source_hash or source_hash == report_fingerprint(report)


def markdown_to_zotero_note_html(markdown: str, title: str) -> str:
    blocks: list[str] = [
        f'<div data-paper-insight-note="{PAPER_INSIGHT_NOTE_MARKER}">',
        f"<h1>{html.escape(title or 'AI 精读笔记')}</h1>",
    ]
    in_list = False
    literal_lines: list[str] | None = None
    literal_marker: str | None = None
    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if literal_lines is not None:
            if line == literal_marker:
                value = "\n".join(literal_lines)
                blocks.append(
                    f'<pre class="math">$${html.escape(value)}$$</pre>'
                    if literal_marker == "$$" else f"<pre>{html.escape(value)}</pre>"
                )
                literal_lines = None
                literal_marker = None
            else:
                literal_lines.append(raw_line)
            continue
        fence = re.match(r"^(`{3,}|~{3,})", line)
        if line == "$$" or fence:
            if in_list:
                blocks.append("</ul>")
                in_list = False
            literal_marker = "$$" if line == "$$" else fence.group(1)
            literal_lines = []
            continue
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
            blocks.append(f"<h{level}>{render_zotero_note_inline_text(heading.group(2))}</h{level}>")
        elif bullet:
            if not in_list:
                blocks.append("<ul>")
                in_list = True
            blocks.append(f"<li>{render_zotero_note_inline_text(bullet.group(1))}</li>")
        else:
            if in_list:
                blocks.append("</ul>")
                in_list = False
            blocks.append(f"<p>{render_zotero_note_inline_text(line)}</p>")
    if literal_lines is not None:
        # Keep incomplete source visible, without pretending it is valid math.
        blocks.append("<pre>" + html.escape((literal_marker or "") + "\n" + "\n".join(literal_lines)) + "</pre>")
    if in_list:
        blocks.append("</ul>")
    blocks.append("<p><em>由 Paper Insight AI 分析生成；请结合原论文核对。</em></p>")
    blocks.append("</div>")
    return "".join(blocks)
