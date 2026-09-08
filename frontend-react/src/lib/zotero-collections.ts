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

/**
 * Returns the collection keys that have at least one child in the rendered
 * tree. Deriving this from the flattened tree keeps the UI safe for malformed
 * Zotero parent references and cycles.
 */
export function getZoteroCollectionKeysWithChildren(
  collections: ZoteroCollection[],
): Set<string> {
  const tree = flattenZoteroCollections(collections);
  const keys = new Set<string>();

  for (let index = 0; index < tree.length - 1; index += 1) {
    if (tree[index + 1].depth > tree[index].depth) {
      keys.add(tree[index].collection_key);
    }
  }

  return keys;
}

/**
 * Keeps collapsed nodes visible while omitting every descendant below them.
 * A depth stack works with the existing flattened representation, including
 * orphaned and cyclic collections that are recovered as rendered roots.
 */
export function getVisibleZoteroCollections(
  collections: ZoteroCollection[],
  collapsedCollectionKeys: ReadonlySet<string>,
): ZoteroCollectionTreeEntry[] {
  const tree = flattenZoteroCollections(collections);
  const visible: ZoteroCollectionTreeEntry[] = [];
  const collapsedAncestorDepths: number[] = [];

  for (const collection of tree) {
    while (
      collapsedAncestorDepths.length > 0
      && collapsedAncestorDepths[collapsedAncestorDepths.length - 1] >= collection.depth
    ) {
      collapsedAncestorDepths.pop();
    }

    if (collapsedAncestorDepths.length === 0) {
      visible.push(collection);
    }

    if (collapsedCollectionKeys.has(collection.collection_key)) {
      collapsedAncestorDepths.push(collection.depth);
    }
  }

  return visible;
}
