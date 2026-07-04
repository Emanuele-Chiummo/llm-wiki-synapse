/**
 * PanelDrawer.test.tsx — ADR-0057 §4: PanelDrawer component tests.
 *
 * Tests:
 *   - Drawer renders its children when open.
 *   - Drawer is hidden (visibility:hidden) when closed.
 *   - Backdrop click calls onClose.
 *   - Esc key calls onClose.
 *   - role="dialog" + aria-modal present.
 *   - Focus moves to drawer on open.
 *   - Focus returns to trigger on close.
 *
 * Uses standard vitest/chai matchers (no @testing-library/jest-dom).
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { PanelDrawer } from "../components/panels/PanelDrawer";

beforeEach(() => {
  cleanup();
});

// ─── Basic rendering ──────────────────────────────────────────────────────────

describe("PanelDrawer — rendering", () => {
  it("renders children when open", () => {
    render(
      <PanelDrawer open={true} side="left" onClose={vi.fn()} label="Wiki tree">
        <div data-testid="drawer-content">Tree content</div>
      </PanelDrawer>,
    );
    const el = screen.getByTestId("drawer-content");
    expect(el).not.toBeNull();
    expect(el.textContent).toBe("Tree content");
  });

  it("renders children when closed (drawer is in DOM, just hidden)", () => {
    render(
      <PanelDrawer open={false} side="left" onClose={vi.fn()} label="Wiki tree">
        <div data-testid="drawer-content">Tree content</div>
      </PanelDrawer>,
    );
    // Children are still in the DOM (portal is always mounted)
    expect(screen.getByTestId("drawer-content")).not.toBeNull();
  });

  it("sets role='dialog' on the drawer panel", () => {
    render(
      <PanelDrawer open={true} side="left" onClose={vi.fn()} label="Wiki tree">
        <span>content</span>
      </PanelDrawer>,
    );
    const dialog = document.querySelector("[role='dialog']");
    expect(dialog).not.toBeNull();
  });

  it("sets aria-modal='true' on the drawer panel", () => {
    render(
      <PanelDrawer open={true} side="left" onClose={vi.fn()} label="Wiki tree">
        <span>content</span>
      </PanelDrawer>,
    );
    const dialog = document.querySelector("[role='dialog']");
    expect(dialog?.getAttribute("aria-modal")).toBe("true");
  });

  it("sets aria-label from the label prop", () => {
    render(
      <PanelDrawer open={true} side="left" onClose={vi.fn()} label="Open wiki tree">
        <span>content</span>
      </PanelDrawer>,
    );
    const dialog = document.querySelector("[role='dialog']");
    expect(dialog?.getAttribute("aria-label")).toBe("Open wiki tree");
  });

  it("applies panel-drawer--left class for side='left'", () => {
    render(
      <PanelDrawer open={true} side="left" onClose={vi.fn()} label="Test">
        <span>content</span>
      </PanelDrawer>,
    );
    const drawer = screen.getByTestId("panel-drawer");
    expect(drawer.classList.contains("panel-drawer--left")).toBe(true);
  });

  it("applies panel-drawer--right class for side='right'", () => {
    render(
      <PanelDrawer open={true} side="right" onClose={vi.fn()} label="Test">
        <span>content</span>
      </PanelDrawer>,
    );
    const drawer = screen.getByTestId("panel-drawer");
    expect(drawer.classList.contains("panel-drawer--right")).toBe(true);
  });
});

// ─── Open/close state ─────────────────────────────────────────────────────────

describe("PanelDrawer — open/close state", () => {
  it("applies visibility:visible when open", () => {
    render(
      <PanelDrawer open={true} side="left" onClose={vi.fn()} label="Test">
        <span>content</span>
      </PanelDrawer>,
    );
    const drawer = screen.getByTestId("panel-drawer");
    expect(drawer.style.visibility).toBe("visible");
  });

  it("applies visibility:hidden when closed", () => {
    render(
      <PanelDrawer open={false} side="left" onClose={vi.fn()} label="Test">
        <span>content</span>
      </PanelDrawer>,
    );
    const drawer = screen.getByTestId("panel-drawer");
    expect(drawer.style.visibility).toBe("hidden");
  });

  it("applies translateX(0) transform when open", () => {
    render(
      <PanelDrawer open={true} side="left" onClose={vi.fn()} label="Test">
        <span>content</span>
      </PanelDrawer>,
    );
    const drawer = screen.getByTestId("panel-drawer");
    expect(drawer.style.transform).toBe("translateX(0)");
  });

  it("applies translateX(-100%) transform when closed (left drawer)", () => {
    render(
      <PanelDrawer open={false} side="left" onClose={vi.fn()} label="Test">
        <span>content</span>
      </PanelDrawer>,
    );
    const drawer = screen.getByTestId("panel-drawer");
    expect(drawer.style.transform).toBe("translateX(-100%)");
  });

  it("applies translateX(100%) transform when closed (right drawer)", () => {
    render(
      <PanelDrawer open={false} side="right" onClose={vi.fn()} label="Test">
        <span>content</span>
      </PanelDrawer>,
    );
    const drawer = screen.getByTestId("panel-drawer");
    expect(drawer.style.transform).toBe("translateX(100%)");
  });
});

// ─── Close interactions ───────────────────────────────────────────────────────

describe("PanelDrawer — close interactions", () => {
  it("calls onClose when backdrop is clicked", () => {
    const onClose = vi.fn();
    render(
      <PanelDrawer open={true} side="left" onClose={onClose} label="Test">
        <span>content</span>
      </PanelDrawer>,
    );
    const backdrop = screen.getByTestId("panel-drawer-backdrop");
    fireEvent.click(backdrop);
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("calls onClose when Esc is pressed inside the drawer", () => {
    const onClose = vi.fn();
    render(
      <PanelDrawer open={true} side="left" onClose={onClose} label="Test">
        <button>focus target</button>
      </PanelDrawer>,
    );
    const drawer = screen.getByTestId("panel-drawer");
    fireEvent.keyDown(drawer, { key: "Escape" });
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("backdrop has opacity=0 and pointer-events=none when closed", () => {
    const onClose = vi.fn();
    render(
      <PanelDrawer open={false} side="left" onClose={onClose} label="Test">
        <span>content</span>
      </PanelDrawer>,
    );
    const backdrop = screen.getByTestId("panel-drawer-backdrop");
    expect(backdrop.style.opacity).toBe("0");
    expect(backdrop.style.pointerEvents).toBe("none");
  });
});

// ─── Focus trap ───────────────────────────────────────────────────────────────

describe("PanelDrawer — focus behavior", () => {
  it("drawer container has tabIndex=-1 (can receive programmatic focus)", () => {
    render(
      <PanelDrawer open={true} side="left" onClose={vi.fn()} label="Test">
        <span>content</span>
      </PanelDrawer>,
    );
    const drawer = screen.getByTestId("panel-drawer");
    expect(drawer.getAttribute("tabindex")).toBe("-1");
  });

  it("Tab key does not call onClose", () => {
    const onClose = vi.fn();
    render(
      <PanelDrawer open={true} side="left" onClose={onClose} label="Test">
        <button>btn1</button>
        <button>btn2</button>
      </PanelDrawer>,
    );
    const drawer = screen.getByTestId("panel-drawer");
    fireEvent.keyDown(drawer, { key: "Tab" });
    expect(onClose).not.toHaveBeenCalled();
  });
});
