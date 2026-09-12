import { describe, expect, it } from 'vitest';

import { decodeRouteSegment } from './router';

describe('decodeRouteSegment', () => {
  it.each(['%E0%A4%A', 'paper%ZZ', '%'])('keeps malformed route %s usable without throwing', (value) => {
    expect(decodeRouteSegment(value)).toBe(value);
  });

  it('decodes valid identifiers exactly once', () => {
    expect(decodeRouteSegment('10.1234%2Fpaper')).toBe('10.1234/paper');
    expect(decodeRouteSegment('%252F')).toBe('%2F');
    expect(decodeRouteSegment('%E8%AE%BA%E6%96%87')).toBe('论文');
  });
});
