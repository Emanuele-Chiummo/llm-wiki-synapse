/**
 * useProviderConfigured.ts — lightweight hook that checks whether at least one
 * provider is configured for the active scope (global or vault).
 *
 * Contract:
 *   - Calls GET /provider/config once on mount (AbortController on unmount, I3).
 *   - Returns { configured: boolean | null, loading: boolean, error: string | null }.
 *   - null means "not yet resolved" (show spinner / nothing, not the gate).
 *   - true  means at least one ProviderConfigItem exists for the scope.
 *   - false means no items → show the provider gate (EmptyState CTA).
 *
 * "Configured" definition: total > 0 from the API response, OR items.length > 0.
 * We do NOT filter by vault_id here — the backend already scopes the list to the
 * active vault's applicable rows (global + vault). A single row from any scope
 * counts as "configured" for the purposes of this gate.
 *
 * INVARIANT I3: fetch once on mount; no Zustand store; AbortController cleanup.
 * No secrets in this file (CLAUDE.md §12).
 */

import { useState, useEffect } from "react";
import { fetchProviderConfigs } from "../api/providerClient";

export interface UseProviderConfiguredResult {
  /** null = still loading; true = at least one provider row exists; false = none. */
  configured: boolean | null;
  loading: boolean;
  error: string | null;
}

export function useProviderConfigured(): UseProviderConfiguredResult {
  const [configured, setConfigured] = useState<boolean | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const ctrl = new AbortController();

    void (async () => {
      try {
        const data = await fetchProviderConfigs(ctrl.signal);
        if (!ctrl.signal.aborted) {
          // total > 0 OR items.length > 0 → at least one provider is configured
          setConfigured(data.total > 0 || data.items.length > 0);
          setError(null);
        }
      } catch (err) {
        if (!ctrl.signal.aborted) {
          setConfigured(false);
          setError(err instanceof Error ? err.message : "Unknown error");
        }
      } finally {
        if (!ctrl.signal.aborted) {
          setLoading(false);
        }
      }
    })();

    return () => ctrl.abort();
  }, []);

  return { configured, loading, error };
}
