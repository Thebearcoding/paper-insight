import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from prompt import PAPER_ANALYSIS_PROMPT, build_open_in_ai_prompt, build_zotero_analysis_prompt


def test_build_open_in_ai_prompt_includes_pdf_url():
    prompt = build_open_in_ai_prompt("https://example.com/paper.pdf")

    assert "https://example.com/paper.pdf" in prompt
    assert "论文 PDF 链接：" in prompt
    assert "{pdf_url}" not in prompt


def test_paper_analysis_prompt_defines_canonical_markdown_structure():
    assert "## 1. 论文解决的任务" in PAPER_ANALYSIS_PROMPT
    assert "禁止输出 `# # 标题`" in PAPER_ANALYSIS_PROMPT
    assert "必须分别独占一行" in PAPER_ANALYSIS_PROMPT
    assert "架构图阅读" in PAPER_ANALYSIS_PROMPT
    assert "材料中未说明" in PAPER_ANALYSIS_PROMPT
    assert "Section 3.2；Figure 2；Table 1" in PAPER_ANALYSIS_PROMPT
    assert "方法链路与训练/推理过程" in PAPER_ANALYSIS_PROMPT
    assert "方法变体与组件区别" in PAPER_ANALYSIS_PROMPT
    assert "设计 → 机制 → 指标" in PAPER_ANALYSIS_PROMPT
    assert "不要只围绕摘要重复" in PAPER_ANALYSIS_PROMPT


def test_zotero_analysis_prompt_includes_grounded_framework_figure():
    prompt = build_zotero_analysis_prompt(
        {
            "label": "Figure 3",
            "caption": "Overview of the two-stage anomaly-aware training pipeline.",
            "page_number": 5,
            "source": "arxiv-html",
        },
        {
            "label": "Table 1",
            "caption": "Comparison with state-of-the-art methods using AUROC and AP.",
            "page_number": 6,
            "source": "pdf-caption-crop",
        },
    )

    assert "Figure 3" in prompt
    assert "two-stage anomaly-aware training pipeline" in prompt
    assert "PDF 第 5 页" in prompt
    assert "输入 → 关键模块/信息流 → 输出" in prompt
    assert "Table 1" in prompt
    assert "PDF 第 6 页" in prompt
    assert "不能仅凭表注猜测" in prompt


def test_zotero_analysis_prompt_does_not_invent_missing_figure():
    prompt = build_zotero_analysis_prompt(None)

    assert "没有提供可确认的论文架构主图信息" in prompt
    assert "不要编造图号" in prompt
    assert "没有识别到可靠的 SOTA 主结果表" in prompt
