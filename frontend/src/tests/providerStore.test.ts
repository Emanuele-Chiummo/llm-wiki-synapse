import { beforeEach, describe, expect, it, vi } from "vitest";

const providerApi = vi.hoisted(() => ({
  fetchProviderConfigs: vi.fn(),
  createProviderConfig: vi.fn(),
  deleteProviderConfig: vi.fn(),
  updateProviderConfig: vi.fn(),
  fetchVendors: vi.fn(),
}));

vi.mock("../api/providerClient", () => providerApi);

import { useProviderStore } from "../store/providerStore";

describe("providerStore mutation failures", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useProviderStore.setState({
      list: [],
      activeItem: null,
      loading: false,
      error: null,
      writeScope: "vault",
      vendors: [],
      vendorsLoading: false,
      vendorsError: null,
    });
  });

  it("stores and propagates an activation failure so the UI cannot report success", async () => {
    providerApi.createProviderConfig.mockRejectedValueOnce(new Error("provider unavailable"));

    const mutation = useProviderStore
      .getState()
      .setActive("api", "model-a", null, "vault", "default");

    await expect(mutation).rejects.toThrow("provider unavailable");
    expect(useProviderStore.getState().error).toBe("provider unavailable");
  });

  it("clears a stale activation error before a successful retry", async () => {
    providerApi.createProviderConfig.mockRejectedValueOnce(new Error("provider unavailable"));

    await expect(
      useProviderStore.getState().setActive("api", "model-a", null, "vault", "default"),
    ).rejects.toThrow("provider unavailable");

    const provider = {
      id: "provider-b",
      scope: "vault",
      vault_id: "default",
      provider_type: "api",
      model_id: "model-b",
      base_url: null,
      max_iter: 3,
      token_budget: 60000,
      is_fallback: false,
      created_at: "2026-07-13T00:00:00Z",
      updated_at: "2026-07-13T00:00:00Z",
    };
    providerApi.createProviderConfig.mockResolvedValueOnce(provider);
    providerApi.fetchProviderConfigs.mockResolvedValueOnce({ items: [provider] });

    await useProviderStore
      .getState()
      .setActive("api", "model-b", null, "vault", "default");

    expect(useProviderStore.getState().error).toBeNull();
    expect(useProviderStore.getState().activeItem?.id).toBe("provider-b");
  });
});
