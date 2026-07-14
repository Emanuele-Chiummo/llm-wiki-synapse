/**
 * NewProjectWizard.test.tsx — vitest unit tests for the New Project Wizard [F1 / WS-E].
 *
 * Covers:
 *  1. Step gating: Next disabled on step 1 until name+parentDir filled.
 *  2. Step gating: Next disabled on step 2 until a language is chosen.
 *  3. "general" scenario is pre-selected on step 3.
 *  4. createProject payload contains name, computed path, scenario, output_language.
 *  5. Success triggers activateProject + window.location.reload.
 *  6. Create error is displayed without reloading.
 *  7. Esc closes the dialog.
 *  8. Backdrop click closes the dialog.
 *  9. Back navigation works step 3 → 2 → 1.
 * 10. Close button is accessible (aria-label).
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

// ─── Mock react-i18next ───────────────────────────────────────────────────────

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => {
      // Return the leaf key for predictable assertions.
      return key.split(".").pop() ?? key;
    },
    i18n: { changeLanguage: vi.fn() },
  }),
}));

// ─── Mock projectsClient ──────────────────────────────────────────────────────

const mockCreateProject = vi.fn();
const mockActivateProject = vi.fn();

vi.mock("../api/projectsClient", () => ({
  createProject: (...args: unknown[]) => mockCreateProject(...args),
  activateProject: (...args: unknown[]) => mockActivateProject(...args),
  fetchProjects: vi.fn().mockResolvedValue({ projects: [], active_id: null }),
  openProject: vi.fn(),
}));

// ─── Mock scenariosClient ─────────────────────────────────────────────────────

const MOCK_SCENARIOS = [
  { id: "general", name: "General", description: "A general-purpose vault." },
  { id: "research", name: "Research", description: "For research projects." },
  { id: "homelab", name: "Homelab", description: "IT infrastructure notes." },
];

const mockFetchScenarios = vi.fn().mockResolvedValue(MOCK_SCENARIOS);

vi.mock("../api/scenariosClient", () => ({
  fetchScenarios: (...args: unknown[]) => mockFetchScenarios(...args),
  applyScenario: vi.fn(),
}));

// ─── Mock window.location.reload ─────────────────────────────────────────────

const mockReload = vi.fn();

// ─── Import component (after mocks) ──────────────────────────────────────────

import { NewProjectWizard } from "../components/projects/NewProjectWizard";

// ─── Helpers ──────────────────────────────────────────────────────────────────

function renderWizard(onClose = vi.fn()) {
  return render(<NewProjectWizard onClose={onClose} />);
}

async function advanceToStep2() {
  fireEvent.change(screen.getByTestId("np-name"), { target: { value: "My Vault" } });
  fireEvent.change(screen.getByTestId("np-parent-dir"), {
    target: { value: "/data/vaults" },
  });
  fireEvent.click(screen.getByTestId("np-next"));
  await waitFor(() => expect(screen.getByTestId("np-language")).toBeTruthy());
}

async function advanceToStep3() {
  await advanceToStep2();
  fireEvent.change(screen.getByTestId("np-language"), { target: { value: "en" } });
  fireEvent.click(screen.getByTestId("np-next-lang"));
  await waitFor(() => expect(screen.getByTestId("np-create")).toBeTruthy());
}

// ─── Setup & teardown ─────────────────────────────────────────────────────────

beforeEach(() => {
  mockCreateProject.mockReset();
  mockActivateProject.mockReset();
  mockFetchScenarios.mockResolvedValue(MOCK_SCENARIOS);

  // Stub window.location.reload
  Object.defineProperty(window, "location", {
    value: { reload: mockReload },
    writable: true,
    configurable: true,
  });
  mockReload.mockReset();
});

afterEach(() => {
  vi.clearAllMocks();
});

// ─── 1. Initial render ────────────────────────────────────────────────────────

describe("NewProjectWizard — initial render", () => {
  it("renders the wizard dialog with role=dialog", () => {
    renderWizard();
    expect(screen.getByRole("dialog")).toBeTruthy();
  });

  it("renders the overlay backdrop", () => {
    renderWizard();
    expect(screen.getByTestId("np-wizard-overlay")).toBeTruthy();
  });

  it("shows step 1 content (name + parent dir) by default", () => {
    renderWizard();
    expect(screen.getByTestId("np-name")).toBeTruthy();
    expect(screen.getByTestId("np-parent-dir")).toBeTruthy();
  });

  it("does NOT show step 2 content on initial render", () => {
    renderWizard();
    expect(screen.queryByTestId("np-language")).toBeNull();
  });

  it("does NOT show step 3 content on initial render", () => {
    renderWizard();
    expect(screen.queryByTestId("np-create")).toBeNull();
  });

  it("renders the step progress indicator with 3 steps", () => {
    renderWizard();
    const progress = screen.getByTestId("np-wizard-progress");
    expect(progress.querySelectorAll("li")).toHaveLength(3);
  });

  it("marks step 1 as current in the progress indicator", () => {
    renderWizard();
    const progress = screen.getByTestId("np-wizard-progress");
    const current = progress.querySelector('[aria-current="step"]');
    expect(current).toBe(progress.querySelectorAll("li")[0]);
  });
});

// ─── 2. Step 1 gating: Next disabled until name + parentDir filled ────────────

describe("NewProjectWizard — step 1 gating", () => {
  it("Next button is disabled when both fields are empty", () => {
    renderWizard();
    const nextBtn = screen.getByTestId("np-next") as HTMLButtonElement;
    expect(nextBtn.disabled).toBe(true);
  });

  it("Next button is disabled when only name is filled", () => {
    renderWizard();
    fireEvent.change(screen.getByTestId("np-name"), { target: { value: "My Vault" } });
    const nextBtn = screen.getByTestId("np-next") as HTMLButtonElement;
    expect(nextBtn.disabled).toBe(true);
  });

  it("Next button is disabled when only parentDir is filled", () => {
    renderWizard();
    fireEvent.change(screen.getByTestId("np-parent-dir"), {
      target: { value: "/data/vaults" },
    });
    const nextBtn = screen.getByTestId("np-next") as HTMLButtonElement;
    expect(nextBtn.disabled).toBe(true);
  });

  it("Next button is enabled when both name and parentDir are filled", () => {
    renderWizard();
    fireEvent.change(screen.getByTestId("np-name"), { target: { value: "My Vault" } });
    fireEvent.change(screen.getByTestId("np-parent-dir"), {
      target: { value: "/data/vaults" },
    });
    const nextBtn = screen.getByTestId("np-next") as HTMLButtonElement;
    expect(nextBtn.disabled).toBe(false);
  });

  it("clicking Next with both fields filled advances to step 2", async () => {
    renderWizard();
    await advanceToStep2();
    expect(screen.queryByTestId("np-name")).toBeNull();
    expect(screen.getByTestId("np-language")).toBeTruthy();
  });
});

// ─── 3. Step 2 gating: Next disabled until a language is chosen ───────────────

describe("NewProjectWizard — step 2 language gating", () => {
  it("Next is disabled when no language selected (empty value)", async () => {
    renderWizard();
    await advanceToStep2();
    const nextBtn = screen.getByTestId("np-next-lang") as HTMLButtonElement;
    expect(nextBtn.disabled).toBe(true);
  });

  it("Next is enabled after choosing a language", async () => {
    renderWizard();
    await advanceToStep2();
    fireEvent.change(screen.getByTestId("np-language"), { target: { value: "en" } });
    const nextBtn = screen.getByTestId("np-next-lang") as HTMLButtonElement;
    expect(nextBtn.disabled).toBe(false);
  });

  it("clicking Next with a language advances to step 3", async () => {
    renderWizard();
    await advanceToStep3();
    expect(screen.queryByTestId("np-language")).toBeNull();
    expect(screen.getByTestId("np-create")).toBeTruthy();
  });

  it("marks step 2 as current in the progress indicator", async () => {
    renderWizard();
    await advanceToStep2();
    const progress = screen.getByTestId("np-wizard-progress");
    const current = progress.querySelector('[aria-current="step"]');
    expect(current).toBe(progress.querySelectorAll("li")[1]);
  });
});

// ─── 4. Step 3: "general" preselected ────────────────────────────────────────

describe("NewProjectWizard — step 3 template", () => {
  it("loads scenarios from GET /scenarios", async () => {
    renderWizard();
    await advanceToStep3();
    await waitFor(() => expect(screen.getByTestId("np-scenario-card-general")).toBeTruthy());
  });

  it("preselects 'general' by default", async () => {
    renderWizard();
    await advanceToStep3();
    await waitFor(() => {
      const card = screen.getByTestId("np-scenario-card-general");
      expect(card.getAttribute("aria-selected")).toBe("true");
    });
  });

  it("allows selecting a different scenario", async () => {
    renderWizard();
    await advanceToStep3();
    await waitFor(() => expect(screen.getByTestId("np-scenario-card-research")).toBeTruthy());
    fireEvent.click(screen.getByTestId("np-scenario-card-research"));
    expect(screen.getByTestId("np-scenario-card-research").getAttribute("aria-selected")).toBe(
      "true",
    );
    expect(screen.getByTestId("np-scenario-card-general").getAttribute("aria-selected")).toBe(
      "false",
    );
  });

  it("marks step 3 as current in the progress indicator", async () => {
    renderWizard();
    await advanceToStep3();
    const progress = screen.getByTestId("np-wizard-progress");
    const current = progress.querySelector('[aria-current="step"]');
    expect(current).toBe(progress.querySelectorAll("li")[2]);
  });
});

// ─── 5. Create payload ────────────────────────────────────────────────────────

describe("NewProjectWizard — create payload", () => {
  beforeEach(() => {
    mockCreateProject.mockResolvedValue({
      id: "proj-123",
      name: "My Vault",
      path: "/data/vaults/my-vault",
      created_at: "2026-01-01T00:00:00Z",
    });
    mockActivateProject.mockResolvedValue({
      project: { id: "proj-123" },
      active_vault_epoch: 1,
    });
  });

  it("sends name + computed path + scenario + output_language to createProject", async () => {
    renderWizard();
    fireEvent.change(screen.getByTestId("np-name"), { target: { value: "My Vault" } });
    fireEvent.change(screen.getByTestId("np-parent-dir"), {
      target: { value: "/data/vaults" },
    });
    fireEvent.click(screen.getByTestId("np-next"));
    await waitFor(() => expect(screen.getByTestId("np-language")).toBeTruthy());

    fireEvent.change(screen.getByTestId("np-language"), { target: { value: "en" } });
    fireEvent.click(screen.getByTestId("np-next-lang"));
    await waitFor(() => expect(screen.getByTestId("np-create")).toBeTruthy());
    await waitFor(() => expect(screen.getByTestId("np-scenario-card-general")).toBeTruthy());

    fireEvent.click(screen.getByTestId("np-create"));

    await waitFor(() => expect(mockCreateProject).toHaveBeenCalledOnce());

    const [nameArg, pathArg, optsArg] = mockCreateProject.mock.calls[0] as [
      string,
      string,
      { scenario: string; output_language: string },
    ];
    expect(nameArg).toBe("My Vault");
    expect(pathArg).toBe("/data/vaults/my-vault");
    expect(optsArg.scenario).toBe("general");
    expect(optsArg.output_language).toBe("en");
  });

  it("slugifies the name correctly in the path", async () => {
    mockCreateProject.mockResolvedValue({
      id: "proj-456",
      name: "Hello World",
      path: "/data/hello-world",
      created_at: "2026-01-01T00:00:00Z",
    });

    renderWizard();
    fireEvent.change(screen.getByTestId("np-name"), { target: { value: "Hello World" } });
    fireEvent.change(screen.getByTestId("np-parent-dir"), {
      target: { value: "/data" },
    });
    fireEvent.click(screen.getByTestId("np-next"));
    await waitFor(() => expect(screen.getByTestId("np-language")).toBeTruthy());
    fireEvent.change(screen.getByTestId("np-language"), { target: { value: "it" } });
    fireEvent.click(screen.getByTestId("np-next-lang"));
    await waitFor(() => expect(screen.getByTestId("np-create")).toBeTruthy());
    await waitFor(() => expect(screen.getByTestId("np-scenario-card-general")).toBeTruthy());
    fireEvent.click(screen.getByTestId("np-create"));

    await waitFor(() => expect(mockCreateProject).toHaveBeenCalledOnce());
    const [, pathArg] = mockCreateProject.mock.calls[0] as [string, string, unknown];
    expect(pathArg).toBe("/data/hello-world");
  });

  it("sends the selected scenario id (not 'general') when changed", async () => {
    renderWizard();
    await advanceToStep3();
    await waitFor(() => expect(screen.getByTestId("np-scenario-card-research")).toBeTruthy());
    fireEvent.click(screen.getByTestId("np-scenario-card-research"));
    fireEvent.click(screen.getByTestId("np-create"));

    await waitFor(() => expect(mockCreateProject).toHaveBeenCalledOnce());
    const [, , optsArg] = mockCreateProject.mock.calls[0] as [
      string,
      string,
      { scenario: string; output_language: string },
    ];
    expect(optsArg.scenario).toBe("research");
  });
});

// ─── 6. Success: activateProject + reload ─────────────────────────────────────

describe("NewProjectWizard — success flow", () => {
  beforeEach(() => {
    mockCreateProject.mockResolvedValue({
      id: "proj-789",
      name: "My Vault",
      path: "/data/vaults/my-vault",
      created_at: "2026-01-01T00:00:00Z",
    });
    mockActivateProject.mockResolvedValue({
      project: { id: "proj-789" },
      active_vault_epoch: 2,
    });
  });

  it("calls activateProject after createProject succeeds", async () => {
    renderWizard();
    await advanceToStep3();
    await waitFor(() => expect(screen.getByTestId("np-scenario-card-general")).toBeTruthy());
    fireEvent.click(screen.getByTestId("np-create"));

    await waitFor(() => expect(mockActivateProject).toHaveBeenCalledOnce());
    expect(mockActivateProject.mock.calls[0]?.[0]).toBe("proj-789");
  });

  it("calls window.location.reload after both API calls succeed", async () => {
    renderWizard();
    await advanceToStep3();
    await waitFor(() => expect(screen.getByTestId("np-scenario-card-general")).toBeTruthy());
    fireEvent.click(screen.getByTestId("np-create"));

    await waitFor(() => expect(mockReload).toHaveBeenCalledOnce());
  });

  it("activateProject is called BEFORE reload", async () => {
    renderWizard();
    await advanceToStep3();
    await waitFor(() => expect(screen.getByTestId("np-scenario-card-general")).toBeTruthy());
    fireEvent.click(screen.getByTestId("np-create"));

    await waitFor(() => expect(mockReload).toHaveBeenCalledOnce());
    expect(mockActivateProject.mock.invocationCallOrder[0]).toBeLessThan(
      mockReload.mock.invocationCallOrder[0] ?? Number.MAX_SAFE_INTEGER,
    );
  });
});

// ─── 7. Error handling ────────────────────────────────────────────────────────

describe("NewProjectWizard — error handling", () => {
  it("shows create error when createProject throws", async () => {
    mockCreateProject.mockRejectedValue(new Error("Server error"));

    renderWizard();
    await advanceToStep3();
    await waitFor(() => expect(screen.getByTestId("np-scenario-card-general")).toBeTruthy());
    fireEvent.click(screen.getByTestId("np-create"));

    await waitFor(() => expect(screen.getByTestId("np-create-error")).toBeTruthy());
    expect(screen.getByTestId("np-create-error").textContent).toContain("Server error");
  });

  it("does NOT call reload when createProject throws", async () => {
    mockCreateProject.mockRejectedValue(new Error("Fail"));

    renderWizard();
    await advanceToStep3();
    await waitFor(() => expect(screen.getByTestId("np-scenario-card-general")).toBeTruthy());
    fireEvent.click(screen.getByTestId("np-create"));

    await waitFor(() => expect(screen.getByTestId("np-create-error")).toBeTruthy());
    expect(mockReload).not.toHaveBeenCalled();
  });

  it("re-enables Create button after an error", async () => {
    mockCreateProject.mockRejectedValue(new Error("Fail"));

    renderWizard();
    await advanceToStep3();
    await waitFor(() => expect(screen.getByTestId("np-scenario-card-general")).toBeTruthy());
    fireEvent.click(screen.getByTestId("np-create"));

    await waitFor(() => {
      const btn = screen.getByTestId("np-create") as HTMLButtonElement;
      expect(btn.disabled).toBe(false);
    });
  });
});

// ─── 8. Dismiss / close ───────────────────────────────────────────────────────

describe("NewProjectWizard — dismiss", () => {
  it("Esc key calls onClose", () => {
    const onClose = vi.fn();
    renderWizard(onClose);
    const dialog = screen.getByTestId("np-wizard-dialog");
    fireEvent.keyDown(dialog, { key: "Escape" });
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("clicking backdrop calls onClose", () => {
    const onClose = vi.fn();
    renderWizard(onClose);
    const overlay = screen.getByTestId("np-wizard-overlay");
    fireEvent.click(overlay);
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("clicking Cancel button on step 1 calls onClose", () => {
    const onClose = vi.fn();
    renderWizard(onClose);
    fireEvent.click(screen.getByTestId("np-cancel"));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("clicking the X close button calls onClose", () => {
    const onClose = vi.fn();
    renderWizard(onClose);
    fireEvent.click(screen.getByTestId("np-close-x"));
    expect(onClose).toHaveBeenCalledOnce();
  });
});

// ─── 9. Back navigation ───────────────────────────────────────────────────────

describe("NewProjectWizard — back navigation", () => {
  it("Back on step 2 returns to step 1", async () => {
    renderWizard();
    await advanceToStep2();
    fireEvent.click(screen.getByTestId("np-back"));
    await waitFor(() => expect(screen.getByTestId("np-name")).toBeTruthy());
    expect(screen.queryByTestId("np-language")).toBeNull();
  });

  it("Back on step 3 returns to step 2", async () => {
    renderWizard();
    await advanceToStep3();
    await waitFor(() => expect(screen.getByTestId("np-scenario-card-general")).toBeTruthy());
    fireEvent.click(screen.getByTestId("np-back-template"));
    await waitFor(() => expect(screen.getByTestId("np-language")).toBeTruthy());
    expect(screen.queryByTestId("np-create")).toBeNull();
  });
});
