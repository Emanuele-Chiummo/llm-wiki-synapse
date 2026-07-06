/**
 * sourcesClient.test.ts — unit tests for the sources API client [F11 / v0.6].
 *
 * Covers:
 *   - SourceDeleteResponse: `deleted_source` field (not `deleted_path`) [Fix #4]
 *   - SourceDeleteFolderResponse: `deleted_source` field aligns with backend [Fix #4]
 *   - deleteSource: DELETEs /sources?path and returns correct shape
 *   - deleteFolderSource: DELETEs /sources?path (dir) and returns correct shape
 *   - listSources: GETs /sources and returns SourceListResponse
 *   - getSourceContent: GETs /sources/content?path and returns SourceContentResponse
 *   - ApiError thrown on non-ok responses
 *
 * Mocks global fetch via vi.stubGlobal.
 * INVARIANT I3: pure async functions; no store subscriptions.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  listSources,
  getSourceContent,
  deleteSource,
  deleteFolderSource,
  sourceRawUrl,
  IngestAllRunningError,
  ingestAllSources,
} from "../api/sourcesClient";
import type {
  SourceDeleteResponse,
  SourceDeleteFolderResponse,
} from "../api/sourcesClient";
import { ApiError } from "../api/graphClient";

// ─── Helpers ──────────────────────────────────────────────────────────────────

function mockFetch(body: unknown, status = 200) {
  return vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    statusText: status === 200 ? "OK" : status === 409 ? "Conflict" : "Error",
    json: () => Promise.resolve(body),
  });
}

beforeEach(() => {
  vi.unstubAllGlobals();
});

// ─── SourceDeleteResponse shape — deleted_source field [Fix #4] ───────────────

describe("sourcesClient — SourceDeleteResponse type [Fix #4]", () => {
  it("deleteSource returns deleted_source (not deleted_path)", async () => {
    const responseBody: SourceDeleteResponse = {
      deleted_source: "reports/q1.pdf",
      pages_deleted: 3,
    };
    const fetchMock = mockFetch(responseBody);
    vi.stubGlobal("fetch", fetchMock);

    const result = await deleteSource("reports/q1.pdf");

    // The field MUST be deleted_source — the type no longer has deleted_path
    expect(result.deleted_source).toBe("reports/q1.pdf");
    expect(result.pages_deleted).toBe(3);
    // TypeScript confirms deleted_path no longer exists on the type (compile-time guard)
    // We verify the runtime shape here:
    expect((result as unknown as Record<string, unknown>)["deleted_path"]).toBeUndefined();
  });

  it("deleteSource calls DELETE /sources?path=<encoded>", async () => {
    const fetchMock = mockFetch({ deleted_source: "my doc.pdf", pages_deleted: 0 });
    vi.stubGlobal("fetch", fetchMock);

    await deleteSource("my doc.pdf");

    const [url, init] = fetchMock.mock.calls[0] as [string, { method: string }];
    expect(url).toContain("/sources");
    expect(url).toContain("path=my%20doc.pdf");
    expect(init.method).toBe("DELETE");
  });

  it("deleteSource throws ApiError on non-ok response", async () => {
    const fetchMock = mockFetch({ detail: "File not found" }, 404);
    vi.stubGlobal("fetch", fetchMock);

    await expect(deleteSource("missing.md")).rejects.toBeInstanceOf(ApiError);
  });
});

// ─── SourceDeleteFolderResponse shape — deleted_source field [Fix #4] ────────

describe("sourcesClient — SourceDeleteFolderResponse type [Fix #4]", () => {
  it("deleteFolderSource returns deleted_source (not deleted_path)", async () => {
    const responseBody: SourceDeleteFolderResponse = {
      deleted_source: "images/",
      files_deleted: 7,
      pages_cascaded: 4,
    };
    const fetchMock = mockFetch(responseBody);
    vi.stubGlobal("fetch", fetchMock);

    const result = await deleteFolderSource("images/");

    expect(result.deleted_source).toBe("images/");
    expect(result.files_deleted).toBe(7);
    expect(result.pages_cascaded).toBe(4);
    // Runtime check: deleted_path must NOT be present in the response shape
    expect((result as unknown as Record<string, unknown>)["deleted_path"]).toBeUndefined();
  });

  it("deleteFolderSource calls DELETE /sources?path=<encoded>", async () => {
    const fetchMock = mockFetch({ deleted_source: "docs/", files_deleted: 2, pages_cascaded: 1 });
    vi.stubGlobal("fetch", fetchMock);

    await deleteFolderSource("docs/sub folder/");

    const [url] = fetchMock.mock.calls[0] as [string];
    expect(url).toContain("/sources");
    expect(url).toContain("path=docs%2Fsub%20folder%2F");
  });

  it("deleteFolderSource throws ApiError on 409 (exceeds max-files cap)", async () => {
    const fetchMock = mockFetch({ detail: "Too many files" }, 409);
    vi.stubGlobal("fetch", fetchMock);

    await expect(deleteFolderSource("huge-dir/")).rejects.toBeInstanceOf(ApiError);
  });
});

// ─── listSources ──────────────────────────────────────────────────────────────

describe("sourcesClient — listSources", () => {
  it("GETs /sources and returns SourceListResponse", async () => {
    const fetchMock = mockFetch({
      entries: [
        { path: "doc.md", name: "doc.md", is_dir: false, ext: ".md", size_bytes: 1024 },
      ],
      total: 1,
      truncated: false,
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await listSources();

    const [url] = fetchMock.mock.calls[0] as [string];
    expect(url).toContain("/sources");
    expect(result.entries).toHaveLength(1);
    expect(result.entries[0]?.name).toBe("doc.md");
    expect(result.truncated).toBe(false);
  });

  it("throws ApiError on non-ok response", async () => {
    const fetchMock = mockFetch({ detail: "Server error" }, 500);
    vi.stubGlobal("fetch", fetchMock);

    await expect(listSources()).rejects.toBeInstanceOf(ApiError);
  });
});

// ─── getSourceContent ─────────────────────────────────────────────────────────

describe("sourcesClient — getSourceContent", () => {
  it("GETs /sources/content?path=<encoded>", async () => {
    const fetchMock = mockFetch({
      path: "report.pdf",
      name: "report.pdf",
      ext: ".pdf",
      size_bytes: 204800,
      mtime: "2026-01-01T00:00:00Z",
      category: "pdf",
      is_text: false,
      ingested: true,
      page_ids: ["page-1"],
    });
    vi.stubGlobal("fetch", fetchMock);

    const result = await getSourceContent("report.pdf");

    const [url] = fetchMock.mock.calls[0] as [string];
    expect(url).toContain("/sources/content");
    expect(url).toContain("path=report.pdf");
    expect(result.category).toBe("pdf");
    expect(result.ingested).toBe(true);
  });
});

// ─── sourceRawUrl ─────────────────────────────────────────────────────────────

describe("sourcesClient — sourceRawUrl", () => {
  it("builds the raw URL with encoded path", () => {
    const url = sourceRawUrl("images/photo 1.jpg");
    expect(url).toContain("/sources/raw");
    expect(url).toContain("path=images%2Fphoto%201.jpg");
  });
});

// ─── ingestAllSources — 409 → IngestAllRunningError ──────────────────────────

describe("sourcesClient — ingestAllSources 409", () => {
  it("throws IngestAllRunningError on 409 (scan already in progress)", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 409,
      statusText: "Conflict",
      json: () => Promise.resolve({ detail: "scan already running" }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(ingestAllSources()).rejects.toBeInstanceOf(IngestAllRunningError);
  });

  it("returns IngestAllResponse on 200", async () => {
    const fetchMock = mockFetch({ started: true, candidate_files: 12 });
    vi.stubGlobal("fetch", fetchMock);

    const result = await ingestAllSources();
    expect(result.started).toBe(true);
    expect(result.candidate_files).toBe(12);
  });
});
