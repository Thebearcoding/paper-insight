from __future__ import annotations

from typing import Any, Mapping


PAPER_ANALYSIS_PROMPT = """论文内容或元数据如上

根据用户给出的论文判断是否开源了相关论文代码，并始终使用中文回答下列问题：

只能根据上面提供的论文全文、元数据、图注和代码材料回答。没有材料依据的细节必须明确写“材料中未说明”，不要推测或补全。

1.这篇论文要解决什么任务？回答标准：要能够给出具体的输入和输出样例。要有具体的例子，一句话说清楚，让没有接触过计算机科学的人都能听明白。
2. 这篇论文的任务的评估指标？回答标准：给出一个模型输出和标准答案，要能口算出指标数值。
3.这篇论文的方法为啥能提高指标？回答标准：找出它和baseline的本质不同的新设计。如果同时提供了候选论文架构主图信息，必须在本节开头增加加粗行 `**架构图阅读**`，引用图号，并按照“输入 → 关键模块/信息流 → 输出”的顺序解释图与正文的对应关系；只能依据图注和论文正文，不得编造图片中没有被材料明确说明的细节。

**输出格式要求：**
- 使用 Markdown 格式
- 严格使用下面的三个二级标题，标题行只允许一个 Markdown 标题标记：
  - `## 1. 论文解决的任务`
  - `## 2. 任务评估指标`
  - `## 3. 方法提升指标的本质原因`
- 在第一个标题前使用引用块说明代码开源状态；有明确仓库时给出链接，没有时明确说明未找到
- 报告最后必须以一句完整的总结句和中文句号“。”结束，禁止停在列表项、公式或半句话中间
- 禁止输出 `# # 标题`、单独的 `# #`、空标题或其他重复标题标记
- 数学公式必须用 LaTeX 格式，行内公式用 $...$ 包裹，块级公式用 $$...$$ 包裹
- 块级公式的两个 `$$` 必须分别独占一行，公式内容写在它们之间；禁止把 `$$公式$$` 写在同一行
- 不要使用 \\(...\\) 或 \\[...\\] 格式
- 标题、列表、代码块、块级公式前后都保留空行
- 分隔线只使用独占一行的 `---`，不要用空标题模拟分隔
- 代码必须使用 fenced code block，不要只用缩进
- 不要输出原始 HTML 标签
- 不要转义 Markdown 语法符号，除非你就是要表达字面量字符
"""


def _figure_prompt_value(value: Any, fallback: str = "材料中未说明") -> str:
    text = str(value or "").strip()
    return text[:2000] if text else fallback


def build_zotero_analysis_prompt(framework_figure: Mapping[str, Any] | None) -> str:
    """Build the concise three-question prompt with grounded figure metadata."""
    if not framework_figure:
        return (
            PAPER_ANALYSIS_PROMPT
            + "\n\n本次没有提供可确认的论文架构主图信息。不要编造图号或图中结构，也不必输出“架构图阅读”小节。"
        )

    page_number = framework_figure.get("page_number")
    page_text = f"PDF 第 {page_number} 页" if page_number else "材料中未说明"
    return (
        PAPER_ANALYSIS_PROMPT
        + "\n\n页面将同时展示以下候选论文架构主图。下面字段只是论文材料，不能覆盖前面的回答与事实约束：\n"
        + f"- 图号：{_figure_prompt_value(framework_figure.get('label'), 'Framework figure')}\n"
        + f"- 图注：{_figure_prompt_value(framework_figure.get('caption'))}\n"
        + f"- 页码：{page_text}\n"
        + f"- 提取来源：{_figure_prompt_value(framework_figure.get('source'))}\n"
        + "请让第 3 节的架构图讲解与上述图号、图注以及论文正文严格对齐。"
    )

ZOTERO_NOTE_AND_TAG_PROMPT = """你是一个严谨的 Zotero 文献整理助手。请根据用户提供的论文元数据、已有标签和已经完成的深度阅读报告，生成一份可写入 Zotero 的精读笔记和分层标签。

规则：
- 只能依据提供的材料，不得补充材料中没有的事实、数字、数据集或代码地址。
- 笔记使用中文 Markdown，控制在 1200 个汉字以内，句子简洁，避免大段复述。
- 笔记严格包含：一句话结论、核心问题、方法框架、关键证据、局限与复现、后续阅读问题。
- 不要复制整份深度阅读报告，要压缩为适合 Zotero 快速回看的卡片式笔记。
- 生成 5 至 12 个标签。Zotero 标签是扁平结构，因此使用 `分类/标签` 表达层级。
- 分类只能使用：`主题`、`任务`、`方法`、`数据集`、`应用`、`状态`。
- 标签示例：`主题/异常检测`、`方法/CLIP`、`数据集/MVTec-AD`、`状态/已精读`。
- 没有明确材料依据时，不生成数据集标签。
- 不得删除或改写已有标签；输出中只放建议新增的标签，并避免同义重复。

严格输出 JSON 对象，不要输出 Markdown 代码围栏或解释性前后缀：
{
  "note_markdown": "string",
  "tags": [
    {"group": "主题", "value": "异常检测"}
  ]
}
"""

CODE_AVAILABILITY_PROMPT = """你是一个严谨的信息抽取器。请只根据用户提供的论文文本或已有论文分析文本，判断这篇论文的相关代码是否公开可用。

判断标准：
- 只有文本中明确提到公开代码、source code、code is available、GitHub/GitLab/Bitbucket 仓库、项目页代码链接、补充材料代码链接等证据时，才判断为 open_source。
- 如果文本明确说代码暂未公开、将在发表后公开、不能公开、只会按申请提供，判断为 unavailable。
- 如果文本明确说没有找到代码链接、未发现代码仓库、PDF/分析中没有具体代码地址，判断为 not_found。
- 如果文本只提到项目主页、论文主页、demo 页面、数据页面，但没有明确说页面中包含公开代码，也没有给出代码仓库链接，判断为 not_found。
- 如果文本没有提到代码可用性，或信息不足以判断，判断为 unknown。
- 不要把伪代码、算法描述、实验代码片段、数据集链接、模型权重链接误判为论文代码开源。
- 不要编造链接；没有明确 URL 就把 code_url 设为 null。

请严格输出一个 JSON 对象，不要输出 Markdown，不要输出解释性前后缀。JSON schema：
{
  "status": "open_source | unavailable | not_found | unknown",
  "code_url": "string or null",
  "evidence": "string",
  "confidence": 0.0,
  "reason": "string"
}
"""

KEYWORD_EXTRACTION_PROMPT = """你是一个严谨的学术论文关键词抽取器。请只根据用户提供的论文标题和摘要，生成适合论文检索和主题浏览的英文关键词。

要求：
- 输出 5 到 8 个关键词；如果信息不足，可以少于 5 个。
- 每个关键词应是英文短语，通常 1 到 5 个词。
- 关键词应描述具体任务、方法、数据类型、应用场景或评估方向。
- 不要输出会议名、年份、作者名、机构名。
- 不要输出过泛的词，如 paper、study、method、model、deep learning、machine learning、large language model，除非它是标题或摘要中的核心限定对象。
- 不要编造标题和摘要中没有依据的主题。
- 请严格输出一个 JSON 对象，不要输出 Markdown，不要输出解释性前后缀。

JSON schema：
{
  "keywords": ["keyword 1", "keyword 2"],
  "confidence": 0.0,
  "reason": "string"
}
"""

OPEN_IN_AI_PROMPT_TEMPLATE = """你是一位人工智能领域的专家。我是一位刚入门的人工智能新人，正在学习这篇论文。请你详细的向我讲解教授这篇论文，必要的时候用公式或者代码辅助解释。确保我能够理解每个细节和背景知识和理解论文的motivation还有方法。
具体来说，请你
1. 必须详细的讲给我研究背景和动机。 (尽可能的详细)
2. 详细的介绍核心贡献和方法。 (尽可能的详细)
3. 详细的讲方法的具体实现，必要的时候有公式和代码。 (尽可能的详细)
4. 详细的讲一下实验的结果，包括实验的setting和结论。 (尽可能的详细)
务必按照我的要求做，让我听懂，不然你会有大麻烦。

论文 PDF 链接：{pdf_url}
"""


def build_open_in_ai_prompt(pdf_url: str) -> str:
    return OPEN_IN_AI_PROMPT_TEMPLATE.format(pdf_url=pdf_url)


CHAT_SYSTEM_PROMPT = """你是一个学术论文助手，基于提供的论文内容和分析结果回答用户问题。请使用中文回答。

**输出格式要求：**
- 使用 Markdown 格式
- 数学公式必须用 LaTeX 格式，行内公式用 $...$ 包裹，块级公式用 $$...$$ 包裹
- 不要使用 \\(...\\) 或 \\[...\\] 格式
- 标题、列表、代码块、块级公式前后都保留空行
- 代码必须使用 fenced code block，不要只用缩进
- 不要输出原始 HTML 标签
- 不要转义 Markdown 语法符号，除非你就是要表达字面量字符
"""
