/**
 * opsClient.test.ts — unit tests for ops trigger request contracts.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { triggerSynthesize } from "../api/opsClient";

function makeMockResponse(body: unknown, status = 202): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("triggerSynthesize", () => {
  it("POSTs /ops/synthesize with an explicit empty JSON body", async () => {
    const mockFetch = vi.fn().mockResolvedValue(makeMockResponse({ status: "started" }));
    vi.stubGlobal("fetch", mockFetch);

    await triggerSynthesize();

    expect(mockFetch).toHaveBeenCalledOnce();
    const url = mockFetch.mock.calls[0]![0] as string;
    const init = mockFetch.mock.calls[0]![1] as {
      method?: string;
      body?: unknown;
      headers?: Record<string, string>;
    };
    expect(url).toMatch(/\/ops\/synthesize$/);
    expect(init.method).toBe("POST");
    expect(init.body).toBe("{}");
    expect(init.headers).toMatchObject({ "Content-Type": "application/json" });
  });
});
