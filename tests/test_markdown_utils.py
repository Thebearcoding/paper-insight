from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from markdown_utils import (
    missing_zotero_report_sections,
    normalize_llm_markdown,
    normalize_zotero_report,
)
from prompt import (
    ZOTERO_DEEP_READING_PROMPT_PARTS,
    ZOTERO_DEEP_READING_SECTION_GROUPS,
)


def test_normalize_llm_markdown_repairs_markdown_and_math():
    normalized = normalize_llm_markdown(
        "\\#Heading\n\\(x_i\\)\n\\[\n\\frac{1}{2}\n\\]\n1)First item",
        analysis_mode=True,
    )

    assert "# Heading" in normalized
    assert "$x_i$" in normalized
    assert "$$\n\\frac{1}{2}\n$$" in normalized
    assert "1) First item" in normalized


def test_normalize_llm_markdown_keeps_code_blocks_literal():
    content = "```python\nvalue = '$x$'\n```"

    assert normalize_llm_markdown(content) == content


def test_normalize_llm_markdown_splits_inline_heading_fragments():
    normalized = normalize_llm_markdown(
        "开源代码仓库链接：https://github.com/lasr-spelling/sae-spelling # 问题1：论文要解决什么任务？",
        analysis_mode=True,
    )

    assert (
        "开源代码仓库链接：https://github.com/lasr-spelling/sae-spelling\n\n# 问题1：论文要解决什么任务？"
        in normalized
    )


def test_normalize_llm_markdown_repairs_production_heading_and_block_math_shape():
    normalized = normalize_llm_markdown(
        "# # 1. 论文解决的任务\n\n# #\n\n核心公式为：\n$$S = 0.5 S_{Loc} + 0.5 S_{Reason}$$\n其中如下。",
        analysis_mode=True,
    )

    assert "# 1. 论文解决的任务" in normalized
    assert "# #" not in normalized
    assert "$$\nS = 0.5 S_{Loc} + 0.5 S_{Reason}\n$$" in normalized


def test_normalize_llm_markdown_production_repairs_are_idempotent():
    content = "## ## 2. 指标\n\n$$F1 = 2 \\times \\frac{PR}{P+R}$$"
    normalized = normalize_llm_markdown(content, analysis_mode=True)

    assert normalize_llm_markdown(normalized, analysis_mode=True) == normalized


def test_normalize_zotero_report_repairs_section_hierarchy():
    report = "\n".join(
        [
            "# Paper report",
            "# 1. Summary",
            "## 2. Background",
            "## 3. Method",
            "## 3.1 Adapter",
            "# 4. Experiments",
            "# 5. Limitations",
            "# 6. Notes",
            "# 7. Checklist",
        ]
    )

    normalized = normalize_zotero_report(report)

    assert "# Paper report" in normalized
    assert "## 1. Summary" in normalized
    assert "## 2. Background" in normalized
    assert "### 3.1 Adapter" in normalized
    assert missing_zotero_report_sections(normalized) == []


def test_missing_zotero_report_sections_detects_truncation():
    incomplete = normalize_zotero_report(
        "# Report\n# 1. Summary\n# 2. Background\n# 3. Method\n# 4. Experiments"
    )

    assert missing_zotero_report_sections(incomplete) == [5, 6, 7]


def test_zotero_segmented_prompts_cover_each_required_section_once():
    combined = "\n".join(prompt for _label, prompt in ZOTERO_DEEP_READING_PROMPT_PARTS)

    assert len(ZOTERO_DEEP_READING_PROMPT_PARTS) == 3
    assert ZOTERO_DEEP_READING_SECTION_GROUPS == ((1, 2, 3), (4, 5), (6, 7))
    for section in range(1, 8):
        assert combined.count(f"## {section}.") == 1
