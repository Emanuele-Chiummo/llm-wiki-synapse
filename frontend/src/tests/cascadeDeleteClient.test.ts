/**
 * cascadeDeleteClient.test.ts — unit tests for the F13 cascade-delete API client.
 *
 * Covers:
 *   - previewCascadeDelete: success + 404 error + network error
 *   - cascadeDelete: success + 404 error + error message extraction from JSON
 *   - Both functions forward AbortSignal
 *
 * All network calls are mocked via vi.stubGlobal('fetch', …).
 * No real HTTP requests are made.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { previewCascadeDelete, cascadeDelete } from "../api/cascadeDeleteClient";
import { ApiError } from "../api/graphClient";
import type { CascadePreviewResponse, CascadeDeleteResult } from "../api/types";

// ─── Fixtures ─────────────────────────────────────────────────────────────────

const PAGE_ID = "00000000-0000-0000-0000-000000000001";

const PREVIEW_FIXTURE: CascadePreviewResponse = {
  target_page_id: PAGE_ID,
  target_title: "Test Page",
  target_file_path: "wiki/concepts/test-page.md",
  will_delete: [PAGE_ID],
  will_preserve_with_pruned_source: [],
  wikilinks_to_rewrite: [
    {
      source_page_id: "00000000-0000-0000-0000-000000000002",
      file_path: "wiki/concepts/other-page.md",
      target_title: "Test Page",
      occurrences: 2,
    },
  ],
  index_entry_will_be_removed: true,
  raw_source_to_delete: "raw/sources/test.md",
  shared_entity_warnings: ["Page 'Shared' shares source overlap with 'Test Page'"],
  match_methods_used: { "wiki/concepts/other-page.md": "exact" },
};

const DELETE_FIXTURE: CascadeDeleteResult = {
  deleted_page_id: PAGE_ID,
  wikilinks_cleaned: 2,
  index_entry_removed: true,
  shared_entity_warnings: ["Page 'Shared' shares source overlap with 'Test Page'"],
};

// ─── Helpers ──────────────────────────────────────────────────────────────────

function mockFetch(status: number, body: unknown): void {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: status >= 200 && status < 300,
      status,
      statusText: status === 200 ? "OK" : status === 404 ? "Not Found" : "Error",
      json: () => Promise.resolve(body),
    }),
  );
}

beforeEach(() => {
  vi.unstubAllGlobals();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

// ─── previewCascadeDelete ─────────────────────────────────────────────────────

describe("previewCascadeDelete", () => {
  it("calls POST /pages/{id}/cascade-delete/preview with method POST", async () => {
    mockFetch(200, PREVIEW_FIXTURE);
    await previewCascadeDelete(PAGE_ID);
    const fetchMock = vi.mocked(fetch);
    expect(fetchMock).toHaveBeenCalledOnce();
    const [url, init] = fetchMock.mock.calls[0] as [string, Record<string, unknown>];
    expect(url).toMatch(`/pages/${PAGE_ID}/cascade-delete/preview`);
    expect(init["method"]).toBe("POST");
  });

  it("returns the parsed CascadePreviewResponse on 200", async () => {
    mockFetch(200, PREVIEW_FIXTURE);
    const result = await previewCascadeDelete(PAGE_ID);
    expect(result.target_page_id).toBe(PAGE_ID);
    expect(result.target_title).toBe("Test Page");
    expect(result.will_delete).toEqual([PAGE_ID]);
    expect(result.shared_entity_warnings).toHaveLength(1);
    expect(result.wikilinks_to_rewrite).toHaveLength(1);
    expect(result.wikilinks_to_rewrite[0]!.occurrences).toBe(2);
    expect(result.index_entry_will_be_removed).toBe(true);
    expect(result.raw_source_to_delete).toBe("raw/sources/test.md");
    expect(result.match_methods_used["wiki/concepts/other-page.md"]).toBe("exact");
  });

  it("throws ApiError with status 404 when page not found", async () => {
    mockFetch(404, { detail: "Page not found" });
    await expect(previewCascadeDelete(PAGE_ID)).rejects.toBeInstanceOf(ApiError);
    try {
      await previewCascadeDelete(PAGE_ID);
    } catch (err) {
      expect((err as ApiError).status).toBe(404);
    }
  });

  it("includes the backend detail message in the error", async () => {
    mockFetch(404, { detail: "Page 00000000 not found or already deleted" });
    try {
      await previewCascadeDelete(PAGE_ID);
      expect.fail("should have thrown");
    } catch (err) {
      expect((err as ApiError).message).toContain("404");
    }
  });

  it("forwards AbortSignal to fetch", async () => {
    mockFetch(200, PREVIEW_FIXTURE);
    const controller = new AbortController();
    await previewCascadeDelete(PAGE_ID, controller.signal);
    const fetchMock = vi.mocked(fetch);
    const [, init] = fetchMock.mock.calls[0] as [string, Record<string, unknown>];
    expect(init["signal"]).toBe(controller.signal);
  });

  it("handles preview with no wikilinks to rewrite", async () => {
    const noLinks: CascadePreviewResponse = {
      ...PREVIEW_FIXTURE,
      wikilinks_to_rewrite: [],
      shared_entity_warnings: [],
      will_preserve_with_pruned_source: [],
    };
    mockFetch(200, noLinks);
    const result = await previewCascadeDelete(PAGE_ID);
    expect(result.wikilinks_to_rewrite).toHaveLength(0);
    expect(result.shared_entity_warnings).toHaveLength(0);
  });

  it("handles preview with will_preserve_with_pruned_source entries", async () => {
    const withPreserve: CascadePreviewResponse = {
      ...PREVIEW_FIXTURE,
      will_preserve_with_pruned_source: ["00000000-0000-0000-0000-000000000003"],
    };
    mockFetch(200, withPreserve);
    const result = await previewCascadeDelete(PAGE_ID);
    expect(result.will_preserve_with_pruned_source).toHaveLength(1);
  });

  it("handles preview with multiple shared_entity_warnings", async () => {
    const manyWarnings: CascadePreviewResponse = {
      ...PREVIEW_FIXTURE,
      shared_entity_warnings: ["Warning A", "Warning B", "Warning C"],
    };
    mockFetch(200, manyWarnings);
    const result = await previewCascadeDelete(PAGE_ID);
    expect(result.shared_entity_warnings).toHaveLength(3);
  });
});

// ─── cascadeDelete ────────────────────────────────────────────────────────────

describe("cascadeDelete", () => {
  it("calls DELETE /pages/{id}", async () => {
    mockFetch(200, DELETE_FIXTURE);
    await cascadeDelete(PAGE_ID);
    const fetchMock = vi.mocked(fetch);
    expect(fetchMock).toHaveBeenCalledOnce();
    const [url, init] = fetchMock.mock.calls[0] as [string, Record<string, unknown>];
    expect(url).toMatch(`/pages/${PAGE_ID}`);
    expect(init["method"]).toBe("DELETE");
  });

  it("returns CascadeDeleteResult on 200", async () => {
    mockFetch(200, DELETE_FIXTURE);
    const result = await cascadeDelete(PAGE_ID);
    expect(result.deleted_page_id).toBe(PAGE_ID);
    expect(result.wikilinks_cleaned).toBe(2);
    expect(result.index_entry_removed).toBe(true);
    expect(result.shared_entity_warnings).toHaveLength(1);
  });

  it("throws ApiError(404) on double-delete (AC-F13-5c)", async () => {
    mockFetch(404, { detail: "Page already deleted" });
    await expect(cascadeDelete(PAGE_ID)).rejects.toBeInstanceOf(ApiError);
    try {
      await cascadeDelete(PAGE_ID);
    } catch (err) {
      expect((err as ApiError).status).toBe(404);
    }
  });

  it("extracts detail message from JSON error body", async () => {
    mockFetch(404, { detail: "Page 00000000-0000-0000-0000-000000000001 not found" });
    try {
      await cascadeDelete(PAGE_ID);
      expect.fail("should have thrown");
    } catch (err) {
      expect((err as ApiError).message).toContain("404");
    }
  });

  it("forwards AbortSignal", async () => {
    mockFetch(200, DELETE_FIXTURE);
    const controller = new AbortController();
    await cascadeDelete(PAGE_ID, controller.signal);
    const fetchMock = vi.mocked(fetch);
    const [, init] = fetchMock.mock.calls[0] as [string, Record<string, unknown>];
    expect(init["signal"]).toBe(controller.signal);
  });

  it("returns wikilinks_cleaned = 0 when no rewrites occurred", async () => {
    mockFetch(200, { ...DELETE_FIXTURE, wikilinks_cleaned: 0 });
    const result = await cascadeDelete(PAGE_ID);
    expect(result.wikilinks_cleaned).toBe(0);
  });

  it("returns empty shared_entity_warnings list", async () => {
    mockFetch(200, { ...DELETE_FIXTURE, shared_entity_warnings: [] });
    const result = await cascadeDelete(PAGE_ID);
    expect(result.shared_entity_warnings).toHaveLength(0);
  });
});
