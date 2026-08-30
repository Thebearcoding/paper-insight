export interface AnalysisAssetSplit {
  beforeAssets: string;
  afterAssets: string;
  matchedSection: boolean;
}

const METHOD_REASON_HEADING = /^#{1,6}[ \t]+3[.．、][ \t]*方法提升指标的本质原因[^\n]*$/m;

export function splitAnalysisAtMethodSection(content: string): AnalysisAssetSplit {
  const match = METHOD_REASON_HEADING.exec(content);
  if (!match || match.index === undefined) {
    return {
      beforeAssets: content,
      afterAssets: '',
      matchedSection: false,
    };
  }

  const headingEnd = match.index + match[0].length;
  return {
    beforeAssets: content.slice(0, headingEnd).trimEnd(),
    afterAssets: content.slice(headingEnd).replace(/^\n+/, ''),
    matchedSection: true,
  };
}
