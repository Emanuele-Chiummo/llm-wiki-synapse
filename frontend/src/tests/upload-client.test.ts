/**
 * upload-client.test.ts — unit tests for uploadDocument (ADR-0020 Feature U / §2)
 *
 * AC-U1: POST /ingest/upload with FormData, no manual Content-Type
 * AC-U2: 202 → UploadResponse parsed correctly (no page_id; status:"queued")
 * AC-U3: 415 → ApiError thrown with status 415
 * AC-U4: 413 → ApiError thrown with status 413
 * AC-U5: AbortSignal is forwarded to fetch
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { uploadDocument } from "../api/ingestClient";
import { ApiError } from "../api/graphClient";

// Inline type for fetch init to avoid ESLint no-undef on the DOM global RequestInit
interface FetchInit {
  method?: string;
  headers?: Record<string, string>;
  body?: unknown;
  signal?: AbortSignal;
}

// ─── Fetch mock helpers ───────────────────────────────────────────────────────

function mockFetch(status: number, body: unknown, headers: Record<string, string> = {}) {
  const response: Response = {
    ok: status >= 200 && status < 300,
    status,
    statusText: String(status),
    headers: new Headers(headers),
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  } as unknown as Response;

  return vi.fn().mockResolvedValue(response);
}

// 202 payload shape (ADR-0020 §2 — non-blocking, no page_id)
const OK_PAYLOAD = {
  file_path: "raw/sources/note.md",
  status: "queued",
  overwritten: false,
};

// ─── AC-U1: FormData is used — NO manual Content-Type ────────────────────────

describe("uploadDocument — request shape (AC-U1)", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", mockFetch(202, OK_PAYLOAD));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("calls POST /ingest/upload with FormData", async () => {
    const file = new File(["# Hello"], "note.md", { type: "text/markdown" });
    await uploadDocument(file);

    const fetchCalls = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls;
    expect(fetchCalls).toHaveLength(1);

    const [url, init] = fetchCalls[0] as [string, FetchInit];
    expect(url).toContain("/ingest/upload");
    expect(init?.method).toBe("POST");

    // CRITICAL: Content-Type must NOT be set manually (browser sets multipart boundary)
    const headers = (init?.headers ?? {}) as Record<string, string>;
    expect("Content-Type" in headers || "content-type" in headers).toBe(false);

    // Body must be FormData
    expect(init?.body).toBeInstanceOf(FormData);
  });

  it("appends file under the 'file' field", async () => {
    const file = new File(["# Hello"], "note.md", { type: "text/markdown" });
    await uploadDocument(file);

    const fetchCalls = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls;
    const [, init] = fetchCalls[0] as [string, FetchInit];
    const form = init?.body as FormData;
    const uploaded = form.get("file") as File;
    expect(uploaded).toBeDefined();
    expect(uploaded.name).toBe("note.md");
  });
});

// ─── AC-U2: 202 response is parsed correctly (no page_id) ────────────────────

describe("uploadDocument — 202 success (AC-U2)", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("resolves with UploadResponse on 202 — file_path + status:queued", async () => {
    const payload = {
      file_path: "raw/sources/test.md",
      status: "queued",
      overwritten: true,
    };
    vi.stubGlobal("fetch", mockFetch(202, payload));

    const file = new File(["content"], "test.md");
    const result = await uploadDocument(file);

    expect(result.file_path).toBe("raw/sources/test.md");
    expect(result.status).toBe("queued");
    expect(result.overwritten).toBe(true);
    // page_id must NOT exist on the response type (ADR-0020 §2 non-blocking shape)
    expect("page_id" in result).toBe(false);
  });

  it("resolves with overwritten:false when file is new", async () => {
    const payload = { file_path: "raw/sources/new.md", status: "queued", overwritten: false };
    vi.stubGlobal("fetch", mockFetch(202, payload));

    const file = new File(["content"], "new.md");
    const result = await uploadDocument(file);
    expect(result.overwritten).toBe(false);
  });
});

// ─── AC-U3: 415 Unsupported Media Type → ApiError ────────────────────────────

describe("uploadDocument — 415 error (AC-U3)", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("throws ApiError with status 415 on unsupported type", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch(415, { detail: "Only .md/.txt/.markdown files are accepted." }),
    );

    const file = new File(["content"], "document.pdf", { type: "application/pdf" });
    await expect(uploadDocument(file)).rejects.toThrow(ApiError);

    try {
      await uploadDocument(file);
    } catch (err) {
      expect(err).toBeInstanceOf(ApiError);
      expect((err as ApiError).status).toBe(415);
    }
  });
});

// ─── AC-U4: 413 Payload Too Large → ApiError ─────────────────────────────────

describe("uploadDocument — 413 error (AC-U4)", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("throws ApiError with status 413 on oversized file", async () => {
    vi.stubGlobal("fetch", mockFetch(413, { detail: "File exceeds 25 MB limit." }));

    const file = new File(["x".repeat(100)], "big.md", { type: "text/markdown" });
    await expect(uploadDocument(file)).rejects.toThrow(ApiError);

    try {
      await uploadDocument(file);
    } catch (err) {
      expect(err).toBeInstanceOf(ApiError);
      expect((err as ApiError).status).toBe(413);
    }
  });
});

// ─── AC-U5: AbortSignal is forwarded ─────────────────────────────────────────

describe("uploadDocument — AbortSignal forwarding (AC-U5)", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("passes the signal to fetch when provided", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch(202, { file_path: "raw/sources/ok.md", status: "queued", overwritten: false }),
    );

    const ctrl = new AbortController();
    const file = new File(["# ok"], "ok.md");
    await uploadDocument(file, ctrl.signal);

    const fetchCalls = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls;
    const [, init] = fetchCalls[0] as [string, FetchInit];
    expect(init?.signal).toBe(ctrl.signal);
  });

  it("omits signal key when no signal is provided", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch(202, { file_path: "raw/sources/ok.md", status: "queued", overwritten: false }),
    );

    const file = new File(["# ok"], "ok.md");
    await uploadDocument(file);

    const fetchCalls = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls;
    const [, init] = fetchCalls[0] as [string, FetchInit];
    // signal may be absent or undefined; it must not be a live AbortSignal
    expect(init?.signal).toBeUndefined();
  });
});
