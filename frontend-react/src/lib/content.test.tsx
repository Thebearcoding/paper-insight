import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import { RichContent } from '@/components/rich-content';

import { normalizeMarkdownContent, splitStreamingMarkdown } from './content';

describe('normalizeMarkdownContent', () => {
  it('normalizes bracket math and markdown markers', () => {
    const normalized = normalizeMarkdownContent(String.raw`\#Heading
\(x_i\)
\[
\frac{1}{2}
\]
1)First item`);

    expect(normalized).toContain('# Heading');
    expect(normalized).toContain('$x_i$');
    expect(normalized).toContain('$$\n\\frac{1}{2}\n$$');
    expect(normalized).toContain('1) First item');
  });

  it('keeps math-like code fenced content untouched', () => {
    const normalized = normalizeMarkdownContent("```python\nvalue = '$x$'\n```");

    expect(normalized).toBe("```python\nvalue = '$x$'\n```");
  });

  it('keeps a leading bold marker instead of turning it into a list item', () => {
    const content = '**手算示例（图像级）**：设 4 张测试图。';

    expect(normalizeMarkdownContent(content, { analysisMode: true })).toBe(content);
  });

  it('repairs a bold bare URL so GFM does not consume the closing marker', () => {
    const content = '代码地址：**https://github.com/7HHHHH/VisualAD**（README）';
    const expected = '代码地址：**<https://github.com/7HHHHH/VisualAD>**（README）';

    expect(normalizeMarkdownContent(content, { analysisMode: true })).toBe(expected);
    expect(normalizeMarkdownContent(expected, { analysisMode: true })).toBe(expected);
  });

  it('splits inline heading fragments onto their own line', () => {
    const normalized = normalizeMarkdownContent(
      '开源代码仓库链接：https://github.com/lasr-spelling/sae-spelling # 问题1：论文要解决什么任务？',
    );

    expect(normalized).toContain('开源代码仓库链接：https://github.com/lasr-spelling/sae-spelling\n\n# 问题1：论文要解决什么任务？');
  });

  it('repairs the duplicated headings and same-line block math seen in production', () => {
    const normalized = normalizeMarkdownContent(
      '# # 1. 论文解决的任务\n\n# #\n\n核心公式为：\n$$S = 0.5 S_{Loc} + 0.5 S_{Reason}$$\n其中如下。',
      { analysisMode: true },
    );

    expect(normalized).toContain('# 1. 论文解决的任务');
    expect(normalized).not.toContain('# #');
    expect(normalized).toContain('$$\nS = 0.5 S_{Loc} + 0.5 S_{Reason}\n$$');
    expect(normalizeMarkdownContent(normalized, { analysisMode: true })).toBe(normalized);
  });
});

describe('splitStreamingMarkdown', () => {
  it('holds an unclosed code fence in the unstable tail', () => {
    const split = splitStreamingMarkdown('Intro line\n```ts\nconst value = 1');

    expect(split.stableContent).toBe('Intro line\n');
    expect(split.unstableContent).toBe('```ts\nconst value = 1');
  });

  it('holds an unclosed inline formula in the unstable tail', () => {
    const split = splitStreamingMarkdown('The answer is $x');

    expect(split.stableContent).toBe('');
    expect(split.unstableContent).toBe('The answer is $x');
  });
});

describe('RichContent', () => {
  it('renders math through the markdown AST renderer', () => {
    const html = renderToStaticMarkup(
      <RichContent content={'The rate is $\\frac{1}{2}$.'} className="markdown-body" />,
    );

    expect(html).toContain('katex');
    expect(html).toContain('The rate is');
  });

  it('renders repaired production block math as display math', () => {
    const html = renderToStaticMarkup(
      <RichContent
        content={'核心公式为：\n$$S = 0.5 S_{Loc} + 0.5 S_{Reason}$$'}
        analysisMode
        className="markdown-body"
      />,
    );

    expect(html).toContain('katex-display');
    expect(html).not.toContain('math-inline');
  });

  it('renders a leading bold label as strong text instead of a list item', () => {
    const html = renderToStaticMarkup(
      <RichContent
        content={'**（1）$F_1$（取阈值 0.5）**：被判为异常的是 A 和 C。'}
        analysisMode
        className="markdown-body"
      />,
    );

    expect(html).toContain('<strong>');
    expect(html).toContain('（1）');
    expect(html).not.toContain('<ul>');
    expect(html).not.toContain('**');
  });

  it('renders a bold bare URL without visible markdown markers', () => {
    const html = renderToStaticMarkup(
      <RichContent
        content={'代码地址：**https://github.com/7HHHHH/VisualAD**（README）'}
        analysisMode
        className="markdown-body"
      />,
    );

    expect(html).toContain('<strong><a href="https://github.com/7HHHHH/VisualAD"');
    expect(html).not.toContain('**');
  });

  it('does not render code blocks as math', () => {
    const html = renderToStaticMarkup(
      <RichContent content={"```text\n$not-math$\n```"} className="markdown-body" />,
    );

    expect(html).not.toContain('katex');
    expect(html).toContain('$not-math$');
  });

  it('renders the unstable streaming tail as plain text', () => {
    const html = renderToStaticMarkup(
      <RichContent content={'Result:\n$$\na + b'} isStreaming className="markdown-body" />,
    );

    expect(html).toContain('rich-content-tail');
    expect(html).toContain('$$');
    expect(html).toContain('a + b');
  });
});
