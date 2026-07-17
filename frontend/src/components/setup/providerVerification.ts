export interface ProviderVerificationIdentity {
  id?: string | undefined;
  provider_type: string;
  model_id: string | null;
  base_url?: string | null | undefined;
  updated_at?: string | undefined;
}

/**
 * Bind a successful probe to the exact persisted provider revision.
 * A new row, model/base URL change, or backend update timestamp invalidates it.
 */
export function providerVerificationFingerprint(provider: ProviderVerificationIdentity): string {
  return JSON.stringify([
    provider.id ?? null,
    provider.provider_type,
    provider.model_id?.trim() ?? "",
    provider.base_url?.trim().replace(/\/+$/, "") ?? "",
    provider.updated_at ?? null,
  ]);
}
