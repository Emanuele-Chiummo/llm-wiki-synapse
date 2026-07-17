/**
 * providerStore.ts — Zustand store for provider configuration (ADR-0018 §4 / F17).
 *
 * INVARIANT I3: separate from graphStore so provider changes never cause the graph to re-render.
 * INVARIANT I6: no provider_type or model_id literals — all values come from GET /provider/config.
 *
 * Active row derivation (ADR-0018 §4):
 *   "most-recent matching row" — POST only creates rows (no upsert endpoint in M4).
 *   We resolve: vault-scoped rows for the current vault_id, sorted by created_at DESC.
 *   Fallback: most-recent global row.
 */

import { create } from "zustand";
import { useShallow } from "zustand/react/shallow";
import type {
  ProviderConfigItem,
  CreateProviderConfigBody,
  UpdateProviderConfigBody,
  VendorInfo,
} from "../api/types";
import {
  fetchProviderConfigs,
  createProviderConfig,
  deleteProviderConfig,
  updateProviderConfig,
  fetchVendors,
} from "../api/providerClient";

// ─── State / Actions ─────────────────────────────────────────────────────────

interface ProviderState {
  list: ProviderConfigItem[];
  activeItem: ProviderConfigItem | null;
  loading: boolean;
  error: string | null;
  /** Scope used for the "set active" POST: "vault" | "global". */
  writeScope: "vault" | "global";
  /** v1.4: vendor catalog from GET /provider/vendors. */
  vendors: VendorInfo[];
  vendorsLoading: boolean;
  vendorsError: string | null;
}

interface ProviderActions {
  fetchList: (signal?: AbortSignal) => Promise<void>;
  setActive: (
    providerType: string,
    modelId: string | null,
    baseUrl: string | null,
    scope: "vault" | "global",
    vaultId: string,
  ) => Promise<void>;
  addProvider: (body: CreateProviderConfigBody, vaultId: string) => Promise<void>;
  deleteProvider: (id: string, vaultId: string) => Promise<void>;
  setWriteScope: (scope: "vault" | "global") => void;
  /** Derive and set the active item from the current list + vaultId. */
  deriveActive: (vaultId: string) => void;
  /** v1.4: fetch the static vendor catalog. */
  fetchVendorCatalog: (signal?: AbortSignal) => Promise<void>;
  /** v1.4: partial-update an existing config row, then re-derive active. */
  updateProvider: (id: string, body: UpdateProviderConfigBody, vaultId: string) => Promise<void>;
  /**
   * FE-UIUX-3: clear the derived active-provider item (and any transient error)
   * on vault switch. `list` is the global provider_config table (not vault-
   * filtered) and is kept — ProviderSelector's vaultId-effect re-derives
   * `activeItem` for the new vault immediately after.
   */
  resetForVault: () => void;
}

export type ProviderStore = ProviderState & ProviderActions;

// ─── Helpers ──────────────────────────────────────────────────────────────────

/**
 * Derive the "active" config for a vault.
 * Precedence: vault-scoped rows (for this vault) DESC created_at > global rows DESC created_at.
 * Non-fallback rows take precedence within a scope.
 */
function deriveActiveItem(list: ProviderConfigItem[], vaultId: string): ProviderConfigItem | null {
  const vaultRows = list
    .filter((r) => r.scope === "vault" && r.vault_id === vaultId)
    .sort((a, b) => b.created_at.localeCompare(a.created_at));

  const primaryVault = vaultRows.find((r) => !r.is_fallback);
  if (primaryVault) return primaryVault;
  if (vaultRows[0]) return vaultRows[0];

  const globalRows = list
    .filter((r) => r.scope === "global")
    .sort((a, b) => b.created_at.localeCompare(a.created_at));

  const primaryGlobal = globalRows.find((r) => !r.is_fallback);
  return primaryGlobal ?? globalRows[0] ?? null;
}

// ─── Store ────────────────────────────────────────────────────────────────────

export const useProviderStore = create<ProviderStore>((set, get) => ({
  list: [],
  activeItem: null,
  loading: false,
  error: null,
  writeScope: "vault",
  vendors: [],
  vendorsLoading: false,
  vendorsError: null,

  fetchList: async (signal) => {
    set({ loading: true, error: null });
    try {
      const res = await fetchProviderConfigs(signal);
      set({ list: res.items, loading: false });
    } catch (err: unknown) {
      if (err instanceof Error && err.name === "AbortError") return;
      set({ error: (err as Error).message, loading: false });
    }
  },

  setActive: async (providerType, modelId, baseUrl, scope, vaultId) => {
    set({ error: null });
    const body: CreateProviderConfigBody = {
      scope,
      vault_id: scope === "vault" ? vaultId : null,
      provider_type: providerType,
      model_id: modelId,
      base_url: baseUrl,
    };
    try {
      await createProviderConfig(body);
      // Refresh list and re-derive active
      const res = await fetchProviderConfigs();
      const newList = res.items;
      set({ list: newList, activeItem: deriveActiveItem(newList, vaultId) });
    } catch (err: unknown) {
      if (err instanceof Error && err.name === "AbortError") return;
      set({ error: (err as Error).message });
      throw err;
    }
  },

  addProvider: async (body, vaultId) => {
    set({ error: null });
    try {
      await createProviderConfig(body);
      const res = await fetchProviderConfigs();
      const newList = res.items;
      set({ list: newList, activeItem: deriveActiveItem(newList, vaultId) });
    } catch (err: unknown) {
      if (err instanceof Error && err.name === "AbortError") return;
      set({ error: (err as Error).message });
      throw err;
    }
  },

  deleteProvider: async (id, vaultId) => {
    set({ error: null });
    try {
      await deleteProviderConfig(id);
      const res = await fetchProviderConfigs();
      const newList = res.items;
      set({ list: newList, activeItem: deriveActiveItem(newList, vaultId) });
    } catch (err: unknown) {
      if (err instanceof Error && err.name === "AbortError") return;
      set({ error: (err as Error).message });
      throw err;
    }
  },

  setWriteScope: (writeScope) => set({ writeScope }),

  deriveActive: (vaultId) => {
    const { list } = get();
    set({ activeItem: deriveActiveItem(list, vaultId) });
  },

  fetchVendorCatalog: async (signal) => {
    set({ vendorsLoading: true, vendorsError: null });
    try {
      const res = await fetchVendors(signal);
      set({ vendors: res.vendors, vendorsLoading: false });
    } catch (err: unknown) {
      if (err instanceof Error && err.name === "AbortError") return;
      set({ vendorsError: (err as Error).message, vendorsLoading: false });
    }
  },

  updateProvider: async (id, body, vaultId) => {
    set({ error: null });
    try {
      await updateProviderConfig(id, body);
      const res = await fetchProviderConfigs();
      const newList = res.items;
      set({ list: newList, activeItem: deriveActiveItem(newList, vaultId) });
    } catch (err: unknown) {
      if (err instanceof Error && err.name === "AbortError") return;
      set({ error: (err as Error).message });
      throw err;
    }
  },

  // FE-UIUX-3
  resetForVault: () => set({ activeItem: null, error: null }),
}));

// ─── Typed selectors (I3) ─────────────────────────────────────────────────────

export function selectProviderList(s: ProviderStore): ProviderConfigItem[] {
  return s.list;
}

export function selectActiveProvider(s: ProviderStore): ProviderConfigItem | null {
  return s.activeItem;
}

export function selectProviderLoading(s: ProviderStore): boolean {
  return s.loading;
}

export function selectProviderError(s: ProviderStore): string | null {
  return s.error;
}

export function selectWriteScope(s: ProviderStore): "vault" | "global" {
  return s.writeScope;
}

export function selectFetchProviderList(s: ProviderStore): ProviderActions["fetchList"] {
  return s.fetchList;
}

export function selectSetActiveProvider(s: ProviderStore): ProviderActions["setActive"] {
  return s.setActive;
}

export function selectAddProvider(s: ProviderStore): ProviderActions["addProvider"] {
  return s.addProvider;
}

export function selectDeleteProvider(s: ProviderStore): ProviderActions["deleteProvider"] {
  return s.deleteProvider;
}

export function selectSetWriteScope(s: ProviderStore): ProviderActions["setWriteScope"] {
  return s.setWriteScope;
}

export function selectDeriveActive(s: ProviderStore): ProviderActions["deriveActive"] {
  return s.deriveActive;
}

// v1.4 selectors

export function selectVendors(s: ProviderStore): VendorInfo[] {
  return s.vendors;
}

export function selectVendorsLoading(s: ProviderStore): boolean {
  return s.vendorsLoading;
}

export function selectVendorsError(s: ProviderStore): string | null {
  return s.vendorsError;
}

export function selectFetchVendorCatalog(s: ProviderStore): ProviderActions["fetchVendorCatalog"] {
  return s.fetchVendorCatalog;
}

export function selectUpdateProvider(s: ProviderStore): ProviderActions["updateProvider"] {
  return s.updateProvider;
}

export function selectProviderResetForVault(s: ProviderStore): ProviderActions["resetForVault"] {
  return s.resetForVault;
}

// ─── Shallow hooks (I3) ───────────────────────────────────────────────────────

/** Hook: provider list — shallow equality. */
export function useProviderList(): ProviderConfigItem[] {
  return useProviderStore(useShallow(selectProviderList));
}

/** Hook: vendor catalog — shallow equality. */
export function useVendorList(): VendorInfo[] {
  return useProviderStore(useShallow(selectVendors));
}
