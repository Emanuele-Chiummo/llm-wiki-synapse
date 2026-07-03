import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiTimeoutError, fetchWithTimeout } from "../api/http";

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("fetchWithTimeout", () => {
  it("aborts slow requests and throws ApiTimeoutError", async () => {
    vi.useFakeTimers();

    vi.stubGlobal(
      "fetch",
      vi.fn((...[_input, init]: Parameters<typeof fetch>) => {
        return new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () => {
            reject(init.signal?.reason ?? new DOMException("Aborted", "AbortError"));
          });
        });
      }),
    );

    const promise = fetchWithTimeout("/slow", {}, 25);
    const assertion = expect(promise).rejects.toBeInstanceOf(ApiTimeoutError);
    await vi.advanceTimersByTimeAsync(25);

    await assertion;
  });

  it("uses the caller signal for normal aborts", async () => {
    const ctrl = new AbortController();
    vi.stubGlobal(
      "fetch",
      vi.fn((...[_input, init]: Parameters<typeof fetch>) => {
        expect(init?.signal).toBeDefined();
        ctrl.abort(new DOMException("Caller abort", "AbortError"));
        return Promise.reject(new DOMException("Caller abort", "AbortError"));
      }),
    );

    await expect(fetchWithTimeout("/abort", { signal: ctrl.signal }, 100)).rejects.toMatchObject({
      name: "AbortError",
    });
  });
});
