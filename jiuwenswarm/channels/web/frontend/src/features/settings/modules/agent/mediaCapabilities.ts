export const mediaCapabilityModalities = ['vision', 'audio', 'video', 'image_gen', 'video_gen'] as const;

export type MediaCapabilityModality = (typeof mediaCapabilityModalities)[number];

export const mediaCapabilityConfigSuffixes = ['api_base', 'api_key', 'model', 'provider'] as const;

export const mediaCapabilityProviderMetadataSuffixes = ['endpoint_profile', 'vendor_key', 'plan'] as const;

export function mediaCapabilityConfigFields(modality: MediaCapabilityModality): string[] {
  return mediaCapabilityConfigSuffixes.map((suffix) => `${modality}_${suffix}`);
}

export function mediaCapabilityProviderMetadataFields(modality: MediaCapabilityModality): string[] {
  return mediaCapabilityProviderMetadataSuffixes.map((suffix) => `${modality}_${suffix}`);
}

export function mediaCapabilityPersistenceFields(modality: MediaCapabilityModality): string[] {
  return [...mediaCapabilityConfigFields(modality), ...mediaCapabilityProviderMetadataFields(modality)];
}

export function mediaCapabilityEnabledField(modality: MediaCapabilityModality): string {
  return `${modality}_enabled`;
}

/**
 * Whether the modality has enough identity to talk to a backend.
 *
 * ``api_base`` + ``provider`` are always required. ``api_key`` / ``model`` may
 * be empty for self-deployed vendors (e.g. vLLM-Omni); dialog validation
 * already enforces them when the selected preset marks them required.
 */
export function isMediaCapabilityConfigured(
  values: Readonly<Record<string, unknown>>,
  modality: MediaCapabilityModality,
): boolean {
  const apiBase = String(values[`${modality}_api_base`] ?? '').trim();
  const provider = String(values[`${modality}_provider`] ?? '').trim();
  return Boolean(apiBase && provider);
}

/** Raw model name from config, or empty when the served default should be used. */
export function mediaCapabilityConfiguredModel(
  values: Readonly<Record<string, unknown>>,
  modality: MediaCapabilityModality,
): string {
  return String(values[`${modality}_model`] ?? '').trim();
}

export function mediaCapabilityVendorKey(
  values: Readonly<Record<string, unknown>>,
  modality: MediaCapabilityModality,
): string {
  return String(values[`${modality}_vendor_key`] ?? '').trim();
}

export function wasConfigAppliedWithoutRestart(result: unknown): boolean {
  return (
    typeof result === 'object' &&
    result !== null &&
    'applied_without_restart' in result &&
    result.applied_without_restart === true
  );
}
