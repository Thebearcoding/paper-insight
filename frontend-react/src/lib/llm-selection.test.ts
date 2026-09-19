import { describe, expect, it } from 'vitest';

import type { SelectableLlmCatalog } from '@/types';
import { modelSelectionValue, parseModelSelection, resolveModelSelection } from './llm-selection';

const catalog: SelectableLlmCatalog = {
  configured: true,
  active_provider_id: 'gateway',
  active_model_name: 'current-model',
  providers: [
    { id: 'empty', name: 'Empty provider', is_active: false, models: [] },
    {
      id: 'gateway', name: 'Gateway', is_active: true,
      models: ['historical-model', 'current-model', 'manual-model'].map((name) => ({
        id: name, provider_id: 'gateway', model_name: name,
      })),
    },
  ],
};
const historical = { provider_id: 'gateway', model_name: 'historical-model' };
const selection = (model: string) => modelSelectionValue('gateway', model);

describe('Zotero request model selection', () => {
  it('uses the current global model when opening a report generated with an older model', () => {
    expect(resolveModelSelection(catalog, '', historical)).toBe(selection('current-model'));
    expect(historical.model_name).toBe('historical-model');
  });

  it('preserves an available model explicitly selected in the page', () => {
    expect(resolveModelSelection(catalog, selection('manual-model'), historical))
      .toBe(selection('manual-model'));
  });

  it('replaces an unavailable selection with the current global model', () => {
    expect(resolveModelSelection(catalog, selection('removed-model'), historical))
      .toBe(selection('current-model'));
  });

  it('uses the historical report model if the global model is unavailable', () => {
    expect(resolveModelSelection({ ...catalog, active_model_name: 'removed-model' }, '', historical))
      .toBe(selection('historical-model'));
  });

  it('falls back to the first available model when the global and report models are unavailable', () => {
    expect(resolveModelSelection(
      { ...catalog, active_provider_id: 'removed-provider' },
      '',
      { provider_id: 'removed-provider', model_name: 'historical-model' },
    )).toBe(selection('historical-model'));
  });

  it('returns no selection for an empty catalog', () => {
    expect(resolveModelSelection({ ...catalog, providers: [] }, '', historical)).toBe('');
  });

  it('preserves provider model identifiers containing URI characters', () => {
    const modelName = 'vendor/model::version?mode=flash';
    expect(parseModelSelection(selection(modelName)))
      .toEqual({ provider_id: 'gateway', model_name: modelName });
    expect(parseModelSelection('gateway::%broken')).toBeNull();
  });
});
