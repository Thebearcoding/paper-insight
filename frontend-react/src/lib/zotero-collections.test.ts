import { describe, expect, it } from 'vitest';

import { flattenZoteroCollections } from './zotero-collections';


describe('flattenZoteroCollections', () => {
  it('keeps siblings together and exposes full paths for duplicate names', () => {
    const result = flattenZoteroCollections([
      { collection_key: 'child-b', collection_version: 1, name: '表面缺陷检测', parent_collection: 'root-b' },
      { collection_key: 'root-a', collection_version: 1, name: '01_研究路线' },
      { collection_key: 'child-a', collection_version: 1, name: '表面缺陷检测', parent_collection: 'root-a' },
      { collection_key: 'root-b', collection_version: 1, name: '98_归档' },
    ]);

    expect(result.map(({ collection_key, depth, path }) => ({ collection_key, depth, path }))).toEqual([
      { collection_key: 'root-a', depth: 0, path: '01_研究路线' },
      { collection_key: 'child-a', depth: 1, path: '01_研究路线 / 表面缺陷检测' },
      { collection_key: 'root-b', depth: 0, path: '98_归档' },
      { collection_key: 'child-b', depth: 1, path: '98_归档 / 表面缺陷检测' },
    ]);
  });

  it('shows collections whose parent is missing as roots', () => {
    const result = flattenZoteroCollections([
      { collection_key: 'orphan', collection_version: 1, name: '待整理', parent_collection: 'missing' },
    ]);

    expect(result[0]).toMatchObject({ collection_key: 'orphan', depth: 0, path: '待整理' });
  });
});
