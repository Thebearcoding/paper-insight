import type { SelectableLlmCatalog } from '@/types';

const MODEL_SELECTION_SEPARATOR = '::';

export interface LlmSelection {
  provider_id: string;
  model_name: string;
}

export function modelSelectionValue(providerId: string, modelName: string): string {
  return `${providerId}${MODEL_SELECTION_SEPARATOR}${encodeURIComponent(modelName)}`;
}

export function parseModelSelection(value: string): LlmSelection | null {
  const separatorIndex = value.indexOf(MODEL_SELECTION_SEPARATOR);
  if (separatorIndex <= 0) return null;
  const providerId = value.slice(0, separatorIndex);
  const encodedModelName = value.slice(separatorIndex + MODEL_SELECTION_SEPARATOR.length);
  try {
    const modelName = decodeURIComponent(encodedModelName);
    return modelName ? { provider_id: providerId, model_name: modelName } : null;
  } catch {
    return null;
  }
}

function catalogHasSelection(catalog: SelectableLlmCatalog, value: string): boolean {
  const selection = parseModelSelection(value);
  return Boolean(selection && catalog.providers.some(
    (provider) => provider.id === selection.provider_id
      && provider.models.some((model) => model.model_name === selection.model_name),
  ));
}

export function resolveModelSelection(
  catalog: SelectableLlmCatalog,
  current: string,
  reportSelection?: LlmSelection | null,
): string {
  if (current && catalogHasSelection(catalog, current)) return current;

  // Use the administrator's current model for new requests. The model that
  // produced an existing report remains separate provenance, not a preference.
  const active = catalog.active_provider_id && catalog.active_model_name
    ? modelSelectionValue(catalog.active_provider_id, catalog.active_model_name)
    : '';
  if (active && catalogHasSelection(catalog, active)) return active;

  const report = reportSelection
    ? modelSelectionValue(reportSelection.provider_id, reportSelection.model_name)
    : '';
  if (report && catalogHasSelection(catalog, report)) return report;

  const firstProvider = catalog.providers.find((provider) => provider.models.length > 0);
  const firstModel = firstProvider?.models[0];
  return firstProvider && firstModel
    ? modelSelectionValue(firstProvider.id, firstModel.model_name)
    : '';
}
