from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from markdown_utils import (
    normalize_llm_markdown,
    normalize_zotero_report,
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


def test_normalize_llm_markdown_keeps_leading_bold_marker():
    content = "**架构图阅读**：Figure 4 展示完整工作流。"

    assert normalize_llm_markdown(content, analysis_mode=True) == content


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
