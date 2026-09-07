import type { ZoteroCollection } from '@/types';


export interface ZoteroCollectionTreeEntry extends ZoteroCollection {
  depth: number;
  path: string;
}

const collectionCollator = new Intl.Collator('zh-CN', {
  numeric: true,
  sensitivity: 'base',
});

function compareCollections(left: ZoteroCollection, right: ZoteroCollection): number {
  return collectionCollator.compare(left.name, right.name)
    || left.collection_key.localeCompare(right.collection_key);
}

export function flattenZoteroCollections(
  collections: ZoteroCollection[],
): ZoteroCollectionTreeEntry[] {
  const byKey = new Map(collections.map((collection) => [collection.collection_key, collection]));
  const children = new Map<string | null, ZoteroCollection[]>();

  for (const collection of collections) {
    const parentKey = collection.parent_collection;
    const resolvedParent = parentKey && parentKey !== collection.collection_key && byKey.has(parentKey)
      ? parentKey
      : null;
    const siblings = children.get(resolvedParent) ?? [];
    siblings.push(collection);
    children.set(resolvedParent, siblings);
  }
  for (const siblings of children.values()) {
    siblings.sort(compareCollections);
  }

  const result: ZoteroCollectionTreeEntry[] = [];
  const visited = new Set<string>();
  const visit = (collection: ZoteroCollection, depth: number, parentPath: string) => {
    if (visited.has(collection.collection_key)) {
      return;
    }
    visited.add(collection.collection_key);
    const path = parentPath ? `${parentPath} / ${collection.name}` : collection.name;
    result.push({ ...collection, depth, path });
    for (const child of children.get(collection.collection_key) ?? []) {
      visit(child, depth + 1, path);
    }
  };

  for (const root of children.get(null) ?? []) {
    visit(root, 0, '');
  }

  // Keep malformed cyclic data visible instead of silently dropping it.
  for (const collection of [...collections].sort(compareCollections)) {
    visit(collection, 0, '');
  }
  return result;
}
