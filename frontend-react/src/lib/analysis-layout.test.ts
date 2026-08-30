import { describe, expect, it } from 'vitest';

import { splitAnalysisAtMethodSection } from './analysis-layout';

describe('splitAnalysisAtMethodSection', () => {
  it('places assets immediately after the third section heading', () => {
    const content = [
      '## 1. 论文解决的任务',
      '',
      '任务内容。',
      '',
      '## 2. 任务评估指标',
      '',
      '指标内容。',
      '',
      '## 3. 方法提升指标的本质原因',
      '',
      '**架构图阅读**：正文。',
    ].join('\n');

    const result = splitAnalysisAtMethodSection(content);

    expect(result.matchedSection).toBe(true);
    expect(result.beforeAssets).toContain('## 3. 方法提升指标的本质原因');
    expect(result.beforeAssets).not.toContain('**架构图阅读**');
    expect(result.afterAssets).toBe('**架构图阅读**：正文。');
  });

  it('keeps malformed legacy reports intact when the section is absent', () => {
    const content = '旧版报告正文';

    expect(splitAnalysisAtMethodSection(content)).toEqual({
      beforeAssets: content,
      afterAssets: '',
      matchedSection: false,
    });
  });
});
