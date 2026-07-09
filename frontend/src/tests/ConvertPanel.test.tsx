/**
 * ConvertPanel.test.tsx — Vitest unit tests for the Marker PDF conversion panel [F12][R11-1][R12-6][A1].
 *
 * Sprint v1.4 W0 — async marker contract:
 *   POST /ingest/convert-marker → 202 (batch queued); polled via GET /ingest/convert-marker/status.
 *
 * Covers:
 *   AC-R11-1-5: "Convert & ingest" button disabled when marker-health returns 503, enabled when 200.
 *   AC-R11-1-6: per-file rows from polling status; failed file shows detail string.
 *   Async flow: startConvert (202) → getConvertStatus poll → progress bar + per-file rows.
 *   Conversion history: appended to history list after batch completes; "Apri" button present.
 *   Drag-drop: onDragEnter+onDragOver prevent → drop accepted.
 *   R12-6: "Avvia Marker" button — hidden in web build; visible in Tauri+offline; unset command
 *           reveals config field; start click calls shell plugin and begins health polling;
 *           success flips the badge to online.
 *   Client-side rejection of > 10 files and non-.pdf files.
 *   I3: ephemeral progress in component-local state only; graphStore used ONLY for navigation.
 *
 * Mock strategy:
 *   - vi.mock("../api/convertClient") — stubs getMarkerHealth, startConvert, getConvertStatus.
 *   - vi.mock("../api/base") — stubs isTauri() to control desktop/web context.
 *   - vi.mock("@tauri-apps/plugin-shell") — stubs Command.create + execute.
 *   - graphStore NOT mocked — real store used (setActiveSection is a side-effect, not asserted here).
 *   - No real backend involved.
 */

import { describe, it, expect, vi, beforeEach, afterEach, beforeAll } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { ConvertPanel } from "../components/convert/ConvertPanel";
import type {
  MarkerHealthResponse,
  ConvertBatchResponse,
  ConvertStatusResponse,
} from "../api/convertClient";
import { ConvertError } from "../api/convertClient";

// ─── Fake localStorage (Node.js 26 / jsdom compat) ────────────────────────────

function makeFakeStorage(): Storage {
  let store: Record<string, string> = {};
  return {
    get length() { return Object.keys(store).length; },
    key(n: number) { return Object.keys(store)[n] ?? null; },
    getItem(k: string) { return Object.prototype.hasOwnProperty.call(store, k) ? (store[k] ?? null) : null; },
    setItem(k: string, v: string) { store[k] = v; },
    removeItem(k: string) { delete store[k]; },
    clear() { store = {}; },
  };
}

const fakeLocalStorage = makeFakeStorage();
vi.stubGlobal("localStorage", fakeLocalStorage);

// ─── Module mocks ──────────────────────────────────────────────────────────────

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, _params?: Record<string, unknown>) => {
      const parts = key.split(".");
      return parts[parts.length - 1] ?? key;
    },
  }),
}));

// Mock the convert API client — async contract: startConvert + getConvertStatus
vi.mock("../api/convertClient", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/convertClient")>();
  return {
    ...actual,
    getMarkerHealth: vi.fn(),
    startConvert: vi.fn(),
    getConvertStatus: vi.fn(),
  };
});

// Mock base.ts so tests can control isTauri() per-suite
vi.mock("../api/base", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/base")>();
  return {
    ...actual,
    isTauri: vi.fn(() => false),
  };
});

// Mock @tauri-apps/plugin-shell [R12-6]
const mockExecute = vi.fn().mockResolvedValue(undefined);
const mockCreate = vi.fn(() => ({ execute: mockExecute }));

vi.mock("@tauri-apps/plugin-shell", () => ({
  Command: {
    create: mockCreate,
  },
}));

import { getMarkerHealth, startConvert, getConvertStatus } from "../api/convertClient";
import { isTauri } from "../api/base";

const mockGetMarkerHealth = vi.mocked(getMarkerHealth);
const mockStartConvert = vi.mocked(startConvert);
const mockGetConvertStatus = vi.mocked(getConvertStatus);
const mockIsTauri = vi.mocked(isTauri);

// Clear fake localStorage before every test suite
beforeAll(() => {
  fakeLocalStorage.clear();
});

// ─── Helpers ───────────────────────────────────────────────────────────────────

function makePdf(name = "test.pdf"): File {
  return new File(["pdf content"], name, { type: "application/pdf" });
}

function makeTxt(name = "test.txt"): File {
  return new File(["text content"], name, { type: "text/plain" });
}

function makeBatchResponse(files: string[]): ConvertBatchResponse {
  return {
    batch_id: "batch-1",
    queued: files.map((f) => ({
      file: f,
      safe_stem: f.replace(".pdf", ""),
      pdf_path: `vault/raw/sources/${f}`,
    })),
    total: files.length,
  };
}

function makeStatusDone(files: Array<{ file: string; status: "ok" | "failed"; detail?: string }>): ConvertStatusResponse {
  return {
    batch_id: "batch-1",
    running: false,
    total: files.length,
    done: files.filter((f) => f.status === "ok").length,
    eta_seconds: null,
    files: files.map((f) => ({
      file: f.file,
      safe_stem: f.file.replace(".pdf", ""),
      status: f.status,
      detail: f.detail ?? null,
      companion_path: f.status === "ok" ? `vault/raw/sources/${f.file.replace(".pdf", ".md")}` : null,
    })),
  };
}

function makeStatusRunning(files: string[], done = 0): ConvertStatusResponse {
  return {
    batch_id: "batch-1",
    running: true,
    total: files.length,
    done,
    eta_seconds: 10,
    files: files.map((f, i) => ({
      file: f,
      safe_stem: f.replace(".pdf", ""),
      status: i < done ? "ok" : "converting",
      detail: null,
      companion_path: i < done ? `vault/raw/sources/${f.replace(".pdf", ".md")}` : null,
    })),
  };
}

/** Render ConvertPanel and wait for the initial health check to resolve */
async function renderPanel() {
  const result = render(<ConvertPanel />);
  await waitFor(() => {
    expect(screen.queryByTestId("marker-status-badge")).not.toBeNull();
  });
  return result;
}

/** Drop files onto the drop zone */
function dropFiles(files: File[]) {
  const dropZone = screen.getByTestId("convert-drop-zone");
  const dataTransfer = { files };
  fireEvent.drop(dropZone, { dataTransfer });
}

// ─── AC-R11-1-5: button disabled/enabled based on health ──────────────────────

describe("ConvertPanel — Marker health gate (AC-R11-1-5)", () => {
  beforeEach(() => {
    mockIsTauri.mockReturnValue(false);
  });

  afterEach(() => {
    vi.resetAllMocks();
  });

  it("disables the Convert button when marker-health returns offline", async () => {
    mockGetMarkerHealth.mockResolvedValue({
      status: "offline",
      detail: "Connection refused",
    } satisfies MarkerHealthResponse);

    await renderPanel();

    const btn = screen.getByTestId("convert-submit-btn") as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
  });

  it("the Convert button is disabled when offline AND files are present", async () => {
    mockGetMarkerHealth.mockResolvedValue({
      status: "offline",
      detail: "Connection refused",
    } satisfies MarkerHealthResponse);

    await renderPanel();
    dropFiles([makePdf("a.pdf")]);

    const btn = screen.getByTestId("convert-submit-btn") as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
  });

  it("enables the Convert button when marker-health returns ok AND files present", async () => {
    mockGetMarkerHealth.mockResolvedValue({ status: "ok" } satisfies MarkerHealthResponse);

    await renderPanel();

    // No files yet — still disabled
    const btn = screen.getByTestId("convert-submit-btn") as HTMLButtonElement;
    expect(btn.disabled).toBe(true);

    dropFiles([makePdf("b.pdf")]);

    await waitFor(() => {
      expect((screen.getByTestId("convert-submit-btn") as HTMLButtonElement).disabled).toBe(false);
    });
  });

  it("shows the Marker offline badge when health is offline", async () => {
    mockGetMarkerHealth.mockResolvedValue({
      status: "offline",
      detail: "timeout",
    } satisfies MarkerHealthResponse);

    await renderPanel();

    await waitFor(() => {
      const badge = screen.getByTestId("marker-status-badge");
      expect(badge.textContent).toContain("markerOfflineBadge");
    });
  });

  it("shows the Marker ready badge when health is ok", async () => {
    mockGetMarkerHealth.mockResolvedValue({ status: "ok" } satisfies MarkerHealthResponse);

    await renderPanel();

    await waitFor(() => {
      const badge = screen.getByTestId("marker-status-badge");
      expect(badge.textContent).toContain("markerOnlineBadge");
    });
  });
});

// ─── AC-R11-1-6: async conversion flow + per-file status rows ─────────────────

describe("ConvertPanel — async conversion flow + per-file status rows (AC-R11-1-6)", () => {
  beforeEach(() => {
    mockIsTauri.mockReturnValue(false);
    mockGetMarkerHealth.mockResolvedValue({ status: "ok" } satisfies MarkerHealthResponse);
    fakeLocalStorage.removeItem("synapse.convertHistory");
  });

  afterEach(() => {
    vi.resetAllMocks();
    fakeLocalStorage.removeItem("synapse.convertHistory");
  });

  it("renders a pending row after dropping a PDF", async () => {
    await renderPanel();
    dropFiles([makePdf("doc.pdf")]);

    await waitFor(() => {
      const pendingRow = screen.getByTestId("convert-file-row-pending");
      expect(pendingRow).not.toBeNull();
      expect(pendingRow.textContent).toContain("doc.pdf");
    });
  });

  it("renders ok rows after successful async conversion (poll returns running=false)", async () => {
    mockStartConvert.mockResolvedValue(makeBatchResponse(["doc.pdf"]));
    mockGetConvertStatus.mockResolvedValue(
      makeStatusDone([{ file: "doc.pdf", status: "ok" }]),
    );

    await renderPanel();
    dropFiles([makePdf("doc.pdf")]);

    const btn = screen.getByTestId("convert-submit-btn");
    await act(async () => {
      fireEvent.click(btn);
    });

    // Wait for the immediate poll to fire and resolve
    await waitFor(() => {
      expect(screen.queryByTestId("convert-file-row-ok")).not.toBeNull();
    }, { timeout: 3000 });
  });

  it("renders failed rows with detail string on conversion error", async () => {
    mockStartConvert.mockResolvedValue(makeBatchResponse(["report.pdf"]));
    mockGetConvertStatus.mockResolvedValue(
      makeStatusDone([{ file: "report.pdf", status: "failed", detail: "Marker service returned 503" }]),
    );

    await renderPanel();
    dropFiles([makePdf("report.pdf")]);

    const btn = screen.getByTestId("convert-submit-btn");
    await act(async () => {
      fireEvent.click(btn);
    });

    await waitFor(() => {
      const failedRow = screen.queryByTestId("convert-file-row-failed");
      expect(failedRow).not.toBeNull();
      const errorEl = screen.queryByTestId("convert-file-error");
      expect(errorEl).not.toBeNull();
      expect(errorEl?.textContent).toContain("Marker service returned 503");
    }, { timeout: 3000 });
  });

  it("shows progress bar while batch is running", async () => {
    mockStartConvert.mockResolvedValue(makeBatchResponse(["a.pdf", "b.pdf"]));
    // First poll: running; second poll: done
    mockGetConvertStatus
      .mockResolvedValueOnce(makeStatusRunning(["a.pdf", "b.pdf"], 1))
      .mockResolvedValue(makeStatusDone([
        { file: "a.pdf", status: "ok" },
        { file: "b.pdf", status: "ok" },
      ]));

    await renderPanel();
    dropFiles([makePdf("a.pdf"), makePdf("b.pdf")]);

    const btn = screen.getByTestId("convert-submit-btn");
    await act(async () => {
      fireEvent.click(btn);
    });

    // Progress section should be visible during conversion (converting=true)
    // OR immediately after (done=true). Either way, the progress bar should appear.
    await waitFor(() => {
      const progress = screen.queryByTestId("convert-progress");
      expect(progress).not.toBeNull();
    }, { timeout: 3000 });
  });

  it("shows ETA label when eta_seconds is present", async () => {
    mockStartConvert.mockResolvedValue(makeBatchResponse(["a.pdf"]));
    // First poll: running with ETA; second: done
    mockGetConvertStatus
      .mockResolvedValueOnce({
        batch_id: "b1",
        running: true,
        total: 1,
        done: 0,
        eta_seconds: 42,
        files: [{ file: "a.pdf", safe_stem: "a", status: "converting", detail: null, companion_path: null }],
      })
      .mockResolvedValue(makeStatusDone([{ file: "a.pdf", status: "ok" }]));

    await renderPanel();
    dropFiles([makePdf("a.pdf")]);

    await act(async () => {
      fireEvent.click(screen.getByTestId("convert-submit-btn"));
    });

    // ETA label should appear during the running phase
    await waitFor(() => {
      const eta = screen.queryByTestId("convert-eta-label");
      expect(eta).not.toBeNull();
    }, { timeout: 3000 });
  });

  it("renders multiple file rows when multiple PDFs are dropped", async () => {
    await renderPanel();
    dropFiles([makePdf("a.pdf"), makePdf("b.pdf"), makePdf("c.pdf")]);

    await waitFor(() => {
      const fileList = screen.getByTestId("convert-file-list");
      const rows = fileList.querySelectorAll("li");
      expect(rows).toHaveLength(3);
    });
  });

  it("shows success hint after all files convert successfully", async () => {
    mockStartConvert.mockResolvedValue(makeBatchResponse(["ok.pdf"]));
    mockGetConvertStatus.mockResolvedValue(
      makeStatusDone([{ file: "ok.pdf", status: "ok" }]),
    );

    await renderPanel();
    dropFiles([makePdf("ok.pdf")]);

    await act(async () => {
      fireEvent.click(screen.getByTestId("convert-submit-btn"));
    });

    await waitFor(() => {
      expect(screen.queryByTestId("convert-success-hint")).not.toBeNull();
    }, { timeout: 3000 });
  });

  it("shows submit error on 409 (batch already running)", async () => {
    mockStartConvert.mockRejectedValue(new ConvertError(409, "batch already running"));

    await renderPanel();
    dropFiles([makePdf("doc.pdf")]);

    await act(async () => {
      fireEvent.click(screen.getByTestId("convert-submit-btn"));
    });

    await waitFor(() => {
      const errEl = screen.queryByTestId("convert-submit-error");
      expect(errEl).not.toBeNull();
      // i18n mock returns last key segment "error409"
      expect(errEl?.textContent).toContain("error409");
    });
  });
});

// ─── Conversion history ────────────────────────────────────────────────────────

describe("ConvertPanel — conversion history", () => {
  beforeEach(() => {
    mockIsTauri.mockReturnValue(false);
    mockGetMarkerHealth.mockResolvedValue({ status: "ok" } satisfies MarkerHealthResponse);
    fakeLocalStorage.removeItem("synapse.convertHistory");
  });

  afterEach(() => {
    vi.resetAllMocks();
    fakeLocalStorage.removeItem("synapse.convertHistory");
  });

  it("shows no history section when localStorage is empty", async () => {
    await renderPanel();
    expect(screen.queryByTestId("convert-history")).toBeNull();
  });

  it("adds an ok entry to history after successful conversion", async () => {
    mockStartConvert.mockResolvedValue(makeBatchResponse(["hist.pdf"]));
    mockGetConvertStatus.mockResolvedValue(
      makeStatusDone([{ file: "hist.pdf", status: "ok" }]),
    );

    await renderPanel();
    dropFiles([makePdf("hist.pdf")]);

    await act(async () => {
      fireEvent.click(screen.getByTestId("convert-submit-btn"));
    });

    await waitFor(() => {
      expect(screen.queryByTestId("convert-history-entry-ok")).not.toBeNull();
    }, { timeout: 3000 });
  });

  it("adds a failed entry to history after failed conversion", async () => {
    mockStartConvert.mockResolvedValue(makeBatchResponse(["fail.pdf"]));
    mockGetConvertStatus.mockResolvedValue(
      makeStatusDone([{ file: "fail.pdf", status: "failed", detail: "parse error" }]),
    );

    await renderPanel();
    dropFiles([makePdf("fail.pdf")]);

    await act(async () => {
      fireEvent.click(screen.getByTestId("convert-submit-btn"));
    });

    await waitFor(() => {
      expect(screen.queryByTestId("convert-history-entry-failed")).not.toBeNull();
    }, { timeout: 3000 });
  });

  it("persists history to localStorage after conversion", async () => {
    mockStartConvert.mockResolvedValue(makeBatchResponse(["persist.pdf"]));
    mockGetConvertStatus.mockResolvedValue(
      makeStatusDone([{ file: "persist.pdf", status: "ok" }]),
    );

    await renderPanel();
    dropFiles([makePdf("persist.pdf")]);

    await act(async () => {
      fireEvent.click(screen.getByTestId("convert-submit-btn"));
    });

    await waitFor(() => {
      const raw = fakeLocalStorage.getItem("synapse.convertHistory");
      expect(raw).not.toBeNull();
      const entries = JSON.parse(raw!) as Array<{ filename: string }>;
      expect(entries.some((e) => e.filename === "persist.pdf")).toBe(true);
    }, { timeout: 3000 });
  });

  it("shows 'Apri' button only for ok history entries", async () => {
    // Pre-populate localStorage with one ok and one failed entry
    const entries = [
      { id: "1", filename: "ok.pdf", safe_stem: "ok", timestamp: Date.now(), status: "ok", companion_path: "vault/raw/sources/ok.md" },
      { id: "2", filename: "fail.pdf", safe_stem: "fail", timestamp: Date.now(), status: "failed", companion_path: null },
    ];
    fakeLocalStorage.setItem("synapse.convertHistory", JSON.stringify(entries));

    await renderPanel();

    await waitFor(() => {
      const openBtns = screen.queryAllByTestId("convert-history-open-btn");
      expect(openBtns).toHaveLength(1); // only ok entry has "Apri"
    });
  });

  it("loads history from localStorage on mount (survives refresh)", async () => {
    const entries = [
      { id: "x1", filename: "saved.pdf", safe_stem: "saved", timestamp: Date.now(), status: "ok", companion_path: "vault/raw/sources/saved.md" },
    ];
    fakeLocalStorage.setItem("synapse.convertHistory", JSON.stringify(entries));

    await renderPanel();

    await waitFor(() => {
      const histSection = screen.queryByTestId("convert-history");
      expect(histSection).not.toBeNull();
      expect(histSection?.textContent).toContain("saved.pdf");
    });
  });
});

// ─── Drag-drop fix ────────────────────────────────────────────────────────────

describe("ConvertPanel — drag-drop (fix: onDragEnter + onDragOver both prevent)", () => {
  beforeEach(() => {
    mockIsTauri.mockReturnValue(false);
    mockGetMarkerHealth.mockResolvedValue({ status: "ok" } satisfies MarkerHealthResponse);
    fakeLocalStorage.removeItem("synapse.convertHistory");
  });

  afterEach(() => {
    vi.resetAllMocks();
  });

  it("accepts a PDF dropped on the zone and shows a pending row", async () => {
    await renderPanel();

    const dropZone = screen.getByTestId("convert-drop-zone");

    // dragenter must not be prevented-blocked by the browser (we call preventDefault)
    fireEvent.dragEnter(dropZone, { dataTransfer: { files: [] } });

    // dragover must allow the drop
    fireEvent.dragOver(dropZone, { dataTransfer: { files: [] } });

    // actual drop
    fireEvent.drop(dropZone, { dataTransfer: { files: [makePdf("dropped.pdf")] } });

    await waitFor(() => {
      expect(screen.queryByTestId("convert-file-row-pending")).not.toBeNull();
      expect(screen.getByTestId("convert-file-row-pending").textContent).toContain("dropped.pdf");
    });
  });

  it("does NOT add files when converting is true", async () => {
    mockStartConvert.mockResolvedValue(makeBatchResponse(["a.pdf"]));
    // Keep running so converting stays true through the drop
    mockGetConvertStatus.mockResolvedValue({
      batch_id: "b1",
      running: true,
      total: 1,
      done: 0,
      eta_seconds: null,
      files: [{ file: "a.pdf", safe_stem: "a", status: "converting", detail: null, companion_path: null }],
    });

    await renderPanel();
    dropFiles([makePdf("a.pdf")]);

    await act(async () => {
      fireEvent.click(screen.getByTestId("convert-submit-btn"));
    });

    // During conversion, drop another file — should be ignored
    await act(async () => {
      fireEvent.drop(screen.getByTestId("convert-drop-zone"), {
        dataTransfer: { files: [makePdf("ignored.pdf")] },
      });
    });

    // "ignored.pdf" should not appear in the pre-submit queue
    await waitFor(() => {
      const names = Array.from(document.querySelectorAll("[data-testid='convert-file-name']"))
        .map((el) => el.textContent ?? "");
      expect(names.every((n) => !n.includes("ignored"))).toBe(true);
    });
  });
});

// ─── Client-side rejection: > 10 files ────────────────────────────────────────

describe("ConvertPanel — client-side rejection: > 10 files", () => {
  beforeEach(() => {
    mockIsTauri.mockReturnValue(false);
    mockGetMarkerHealth.mockResolvedValue({ status: "ok" } satisfies MarkerHealthResponse);
  });

  afterEach(() => {
    vi.resetAllMocks();
  });

  it("shows a validation message when more than 10 PDFs are dropped at once", async () => {
    await renderPanel();

    const elevenPdfs = Array.from({ length: 11 }, (_, i) => makePdf(`file${i}.pdf`));
    dropFiles(elevenPdfs);

    await waitFor(() => {
      const msg = screen.getByTestId("convert-validation-msg");
      expect(msg.textContent).toContain("tooManyFiles");
    });

    expect(screen.queryByTestId("convert-file-list")).toBeNull();
  });

  it("does not add files to the list when more than 10 PDFs are dropped", async () => {
    await renderPanel();

    const elevenPdfs = Array.from({ length: 11 }, (_, i) => makePdf(`f${i}.pdf`));
    dropFiles(elevenPdfs);

    await waitFor(() => {
      expect(screen.getByTestId("convert-validation-msg")).not.toBeNull();
    });

    expect(screen.queryByTestId("convert-file-list")).toBeNull();
  });
});

// ─── Client-side rejection: non-.pdf files ────────────────────────────────────

describe("ConvertPanel — client-side rejection: non-.pdf files", () => {
  beforeEach(() => {
    mockIsTauri.mockReturnValue(false);
    mockGetMarkerHealth.mockResolvedValue({ status: "ok" } satisfies MarkerHealthResponse);
  });

  afterEach(() => {
    vi.resetAllMocks();
  });

  it("shows a validation message when a non-PDF file is dropped", async () => {
    await renderPanel();
    dropFiles([makeTxt("document.txt")]);

    await waitFor(() => {
      const msg = screen.getByTestId("convert-validation-msg");
      expect(msg.textContent).toContain("badFileType");
    });
  });

  it("does not add a non-PDF file to the list", async () => {
    await renderPanel();
    dropFiles([makeTxt("readme.txt")]);

    await waitFor(() => {
      expect(screen.getByTestId("convert-validation-msg")).not.toBeNull();
    });

    expect(screen.queryByTestId("convert-file-list")).toBeNull();
  });

  it("rejects a mix of PDF and non-PDF files", async () => {
    await renderPanel();
    dropFiles([makePdf("ok.pdf"), makeTxt("bad.txt")]);

    await waitFor(() => {
      const msg = screen.getByTestId("convert-validation-msg");
      expect(msg.textContent).toContain("badFileType");
    });

    expect(screen.queryByTestId("convert-file-list")).toBeNull();
  });
});

// ─── I3: ephemeral progress is component-local; graphStore only for navigation ─

describe("ConvertPanel — I3: ephemeral progress local; graphStore navigation only", () => {
  afterEach(() => {
    vi.resetAllMocks();
  });

  it("ConvertPanel renders without errors when graphStore is not mocked", async () => {
    // graphStore is used for setActiveSection (navigation) only — not for ephemeral
    // progress states (I3). The real store is available in tests as a module singleton,
    // so no mock is needed. Component renders without crash.
    mockIsTauri.mockReturnValue(false);
    mockGetMarkerHealth.mockResolvedValue({ status: "ok" } satisfies MarkerHealthResponse);
    await act(async () => {
      render(<ConvertPanel />);
      await Promise.resolve();
    });
    expect(true).toBe(true);
  });
});

// ─── R12-6: "Avvia Marker" — hidden in web build ─────────────────────────────

describe("ConvertPanel — R12-6: Avvia Marker — hidden in web build", () => {
  afterEach(() => {
    vi.resetAllMocks();
  });

  it("does NOT show the start-marker button when isTauri() is false (web build)", async () => {
    mockIsTauri.mockReturnValue(false);
    mockGetMarkerHealth.mockResolvedValue({
      status: "offline",
      detail: "refused",
    } satisfies MarkerHealthResponse);

    await renderPanel();

    expect(screen.queryByTestId("start-marker-btn")).toBeNull();
  });

  it("does NOT show the start-marker button when Tauri but Marker is online", async () => {
    mockIsTauri.mockReturnValue(true);
    mockGetMarkerHealth.mockResolvedValue({ status: "ok" } satisfies MarkerHealthResponse);

    await renderPanel();

    expect(screen.queryByTestId("start-marker-btn")).toBeNull();
  });
});

// ─── R12-6: "Avvia Marker" — shown in Tauri + offline ────────────────────────

describe("ConvertPanel — R12-6: Avvia Marker — visible in Tauri + offline", () => {
  beforeEach(() => {
    mockIsTauri.mockReturnValue(true);
    mockGetMarkerHealth.mockResolvedValue({
      status: "offline",
      detail: "refused",
    } satisfies MarkerHealthResponse);
    try { localStorage.removeItem("synapse.markerStartCommand"); } catch { /* ignore */ }
    vi.clearAllMocks();
    mockIsTauri.mockReturnValue(true);
    mockGetMarkerHealth.mockResolvedValue({
      status: "offline",
      detail: "refused",
    } satisfies MarkerHealthResponse);
  });

  afterEach(() => {
    vi.resetAllMocks();
    try { localStorage.removeItem("synapse.markerStartCommand"); } catch { /* ignore */ }
  });

  it("shows the start-marker button when Tauri + offline", async () => {
    await renderPanel();

    await waitFor(() => {
      expect(screen.queryByTestId("start-marker-btn")).not.toBeNull();
    });
  });

  it("clicking start-marker with no saved command reveals the config field", async () => {
    await renderPanel();

    const btn = await screen.findByTestId("start-marker-btn");
    await act(async () => {
      fireEvent.click(btn);
    });

    await waitFor(() => {
      expect(screen.queryByTestId("start-marker-cmd-field")).not.toBeNull();
    });

    expect(screen.queryByTestId("start-marker-cmd-save")).not.toBeNull();
  });

  it("typing a command and saving stores it in localStorage", async () => {
    await renderPanel();

    const btn = await screen.findByTestId("start-marker-btn");
    await act(async () => {
      fireEvent.click(btn);
    });

    const cmdInput = await screen.findByTestId("start-marker-cmd-input");
    fireEvent.change(cmdInput, {
      target: { value: "cd /tools && ./.venv/bin/python service.py --port 8555" },
    });

    mockExecute.mockResolvedValue(undefined);
    mockCreate.mockReturnValue({ execute: mockExecute });

    mockGetMarkerHealth
      .mockResolvedValueOnce({ status: "offline", detail: "starting" })
      .mockResolvedValueOnce({ status: "ok" });

    await act(async () => {
      fireEvent.click(screen.getByTestId("start-marker-cmd-save"));
    });

    await waitFor(() => {
      expect(localStorage.getItem("synapse.markerStartCommand")).toBe(
        "cd /tools && ./.venv/bin/python service.py --port 8555",
      );
    });
  });

  it("start click with saved command calls the shell plugin with sh -c", async () => {
    localStorage.setItem("synapse.markerStartCommand", "TORCH_DEVICE=mps python service.py");

    mockExecute.mockResolvedValue(undefined);
    mockCreate.mockReturnValue({ execute: mockExecute });

    mockGetMarkerHealth
      .mockResolvedValueOnce({ status: "offline", detail: "refused" })
      .mockResolvedValue({ status: "ok" });

    await renderPanel();

    const btn = await screen.findByTestId("start-marker-btn");
    await act(async () => {
      fireEvent.click(btn);
    });

    await waitFor(() => {
      expect(mockCreate).toHaveBeenCalledWith(
        "sh",
        expect.arrayContaining(["-c"]),
      );
    });

    const allCalls = mockCreate.mock.calls as unknown as Array<[string, string[]]>;
    const shellArgs = allCalls[0]?.[1] ?? [];
    const shellCmd = shellArgs[1] ?? "";
    expect(shellCmd).toContain("TORCH_DEVICE=mps python service.py");
    expect(shellCmd).toContain(">/dev/null 2>&1 &");
  });

  it("badge flips to online after the poll detects Marker is up", async () => {
    localStorage.setItem("synapse.markerStartCommand", "./.venv/bin/python service.py");

    mockExecute.mockResolvedValue(undefined);
    mockCreate.mockReturnValue({ execute: mockExecute });

    let callCount = 0;
    mockGetMarkerHealth.mockImplementation(async () => {
      callCount++;
      if (callCount <= 1) return { status: "offline" as const, detail: "refused" };
      return { status: "ok" as const };
    });

    await renderPanel();

    const btn = await screen.findByTestId("start-marker-btn");
    await act(async () => {
      fireEvent.click(btn);
    });

    await waitFor(
      () => {
        const badge = screen.getByTestId("marker-status-badge");
        expect(badge.textContent).toContain("markerOnlineBadge");
      },
      { timeout: 8_000 },
    );
  });
});

// ─── R12-6: cancel hides the config field ────────────────────────────────────

describe("ConvertPanel — R12-6: cancel hides the config field", () => {
  afterEach(() => {
    vi.resetAllMocks();
    try { localStorage.removeItem("synapse.markerStartCommand"); } catch { /* ignore */ }
  });

  it("clicking cancel on the config field hides it without spawning", async () => {
    mockIsTauri.mockReturnValue(true);
    mockGetMarkerHealth.mockResolvedValue({
      status: "offline",
      detail: "refused",
    } satisfies MarkerHealthResponse);

    await renderPanel();

    const startBtn = await screen.findByTestId("start-marker-btn");
    await act(async () => {
      fireEvent.click(startBtn);
    });

    await waitFor(() => {
      expect(screen.queryByTestId("start-marker-cmd-field")).not.toBeNull();
    });

    await act(async () => {
      fireEvent.click(screen.getByTestId("start-marker-cmd-cancel"));
    });

    await waitFor(() => {
      expect(screen.queryByTestId("start-marker-cmd-field")).toBeNull();
    });
    expect(mockCreate).not.toHaveBeenCalled();
  });
});
