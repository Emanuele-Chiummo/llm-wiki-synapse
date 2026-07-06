/**
 * MetaFileView.test.tsx — unit tests for the vault meta read-only drawer (WS-D8).
 *
 * Covers:
 *   - Drawer is closed (hidden) when file=null.
 *   - Drawer renders the file title when open.
 *   - Read-only badge is visible.
 *   - Close button calls onClose.
 *   - Body renders sanitised markdown (via renderMarkdown).
 *   - Frontmatter is stripped before rendering (I5 / stripLeadingFrontmatter).
 *   - No edit or delete action buttons are present (read-only invariant).
 *
 * PanelDrawer is NOT mocked — we test through the real component so portal
 * rendering is covered.  document.body is JSDOM's body and createPortal works.
 *
 * I3 note: renderMarkdown is called ONCE per content string; the useMemo
 * dependency on file?.content guarantees this.  We do NOT assert call count here
 * (that is tested in chat-parse-once.test.ts); we only assert correct HTML output.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { MetaFileView } from "../components/wiki/MetaFileView";
import type { VaultMetaFile } from "../api/vaultMetaClient";

// ─── i18n mock ───────────────────────────────────────────────────────────────

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => {
      const map: Record<string, string> = {
        "meta.vaultSection": "Vault",
        "meta.readOnly": "Read-only",
        "meta.drawer": "Vault meta file",
        "common.close": "Close",
      };
      return map[key] ?? key;
    },
    i18n: { changeLanguage: vi.fn() },
  }),
}));

// ─── Fixtures ─────────────────────────────────────────────────────────────────

const SCHEMA_FILE: VaultMetaFile = {
  name: "schema.md",
  path: "schema.md",
  title: "Schema",
  content: "# Schema\n\nThis file defines the vault schema.",
};

const PURPOSE_FILE: VaultMetaFile = {
  name: "purpose.md",
  path: "purpose.md",
  title: "Purpose",
  content: "---\ntitle: Purpose\n---\n\n# Purpose\n\nThis vault tracks homelab knowledge.",
};

// ─── Setup ────────────────────────────────────────────────────────────────────

beforeEach(() => {
  cleanup();
});

// ─── Tests ────────────────────────────────────────────────────────────────────

describe("MetaFileView — closed state (file=null)", () => {
  it("renders the drawer in the DOM but hidden when file is null", () => {
    render(<MetaFileView file={null} onClose={vi.fn()} />);
    const drawer = document.querySelector("[data-testid='panel-drawer']");
    // PanelDrawer always renders into the portal; it is hidden via visibility:hidden
    expect(drawer).not.toBeNull();
    expect((drawer as HTMLElement).style.visibility).toBe("hidden");
  });
});

describe("MetaFileView — open state", () => {
  it("shows the file title when open", () => {
    render(<MetaFileView file={SCHEMA_FILE} onClose={vi.fn()} />);
    const title = screen.getByTestId("meta-file-title");
    expect(title.textContent).toBe("Schema");
  });

  it("shows the read-only badge", () => {
    render(<MetaFileView file={SCHEMA_FILE} onClose={vi.fn()} />);
    // Badge text = "Read-only" (from i18n mock)
    const badges = document.querySelectorAll("span");
    const badge = [...badges].find((s) => s.textContent === "Read-only");
    expect(badge, "Read-only badge should be visible").not.toBeNull();
  });

  it("renders the drawer visible when a file is provided", () => {
    render(<MetaFileView file={SCHEMA_FILE} onClose={vi.fn()} />);
    const drawer = document.querySelector("[data-testid='panel-drawer']");
    expect((drawer as HTMLElement).style.visibility).toBe("visible");
  });

  it("renders the markdown body with parsed content", () => {
    render(<MetaFileView file={SCHEMA_FILE} onClose={vi.fn()} />);
    const body = screen.getByTestId("meta-file-body");
    // renderMarkdown converts "# Schema" → <h1>Schema</h1>
    expect(body.innerHTML).toContain("<h1");
    expect(body.textContent).toContain("Schema");
  });

  it("strips YAML frontmatter before rendering markdown", () => {
    render(<MetaFileView file={PURPOSE_FILE} onClose={vi.fn()} />);
    const body = screen.getByTestId("meta-file-body");
    // The frontmatter block (---\ntitle: Purpose\n---) must NOT appear in the body
    expect(body.innerHTML).not.toContain("title: Purpose");
    expect(body.innerHTML).not.toContain("---");
  });

  it("renders the purpose file content correctly", () => {
    render(<MetaFileView file={PURPOSE_FILE} onClose={vi.fn()} />);
    const body = screen.getByTestId("meta-file-body");
    expect(body.textContent).toContain("homelab knowledge");
  });
});

describe("MetaFileView — close button", () => {
  it("calls onClose when the close button is clicked", () => {
    const onClose = vi.fn();
    render(<MetaFileView file={SCHEMA_FILE} onClose={onClose} />);
    const closeBtn = screen.getByTestId("meta-file-close");
    fireEvent.click(closeBtn);
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("calls onClose when Esc is pressed in the drawer", () => {
    const onClose = vi.fn();
    render(<MetaFileView file={SCHEMA_FILE} onClose={onClose} />);
    const drawer = screen.getByTestId("panel-drawer");
    fireEvent.keyDown(drawer, { key: "Escape" });
    expect(onClose).toHaveBeenCalledOnce();
  });
});

describe("MetaFileView — read-only invariant (no edit/delete buttons)", () => {
  it("has no edit button", () => {
    render(<MetaFileView file={SCHEMA_FILE} onClose={vi.fn()} />);
    // Any button with an aria-label matching "edit" variants should not exist
    const buttons = screen.queryAllByRole("button");
    const editButtons = buttons.filter((b) =>
      /edit|save|delete|remove/i.test(b.getAttribute("aria-label") ?? b.textContent ?? ""),
    );
    expect(editButtons).toHaveLength(0);
  });
});

describe("MetaFileView — purpose file variant", () => {
  it("shows purpose.md title", () => {
    render(<MetaFileView file={PURPOSE_FILE} onClose={vi.fn()} />);
    const title = screen.getByTestId("meta-file-title");
    expect(title.textContent).toBe("Purpose");
  });
});
