/** @vitest-environment jsdom */

/**
 * ApiTokensCard.test.tsx — PF-AUTH-1 (1.9.4 W4) scoped API token management UI.
 *
 * Coverage:
 *   - Renders the empty state when no tokens exist.
 *   - Lists active tokens with scope/access/last-used columns.
 *   - Create flow: posts label/vault_id/read_only, shows the plaintext ONCE in the
 *     reveal dialog, reloads the list.
 *   - The reveal dialog's plaintext disappears after "Done" is clicked.
 *   - Revoke flow: confirms, calls revokeApiToken, reloads the list.
 *   - Load error surfaces a visible message.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

const fetchApiTokens = vi.fn();
const createApiToken = vi.fn();
const revokeApiToken = vi.fn();
vi.mock("../api/apiTokensClient", () => ({
  fetchApiTokens: (...args: unknown[]) => fetchApiTokens(...args),
  createApiToken: (...args: unknown[]) => createApiToken(...args),
  revokeApiToken: (...args: unknown[]) => revokeApiToken(...args),
}));

import { ApiTokensCard } from "../components/settings/sections/ApiTokensCard";

beforeEach(() => {
  fetchApiTokens.mockReset();
  createApiToken.mockReset();
  revokeApiToken.mockReset();
  vi.spyOn(window, "confirm").mockReturnValue(true);
});

describe("ApiTokensCard — empty + list states", () => {
  it("shows the empty state when there are no active tokens", async () => {
    fetchApiTokens.mockResolvedValue({ tokens: [] });
    render(<ApiTokensCard />);

    expect(await screen.findByText("settings.security.apiTokens.empty")).toBeTruthy();
  });

  it("lists active tokens with scope/access/last-used columns", async () => {
    fetchApiTokens.mockResolvedValue({
      tokens: [
        {
          id: "tok-1",
          label: "CI runner",
          vault_id: null,
          read_only: false,
          created_at: "2026-07-01T00:00:00Z",
          last_used_at: null,
        },
        {
          id: "tok-2",
          label: "readonly-scoped",
          vault_id: "other-vault",
          read_only: true,
          created_at: "2026-07-02T00:00:00Z",
          last_used_at: "2026-07-10T00:00:00Z",
        },
      ],
    });
    render(<ApiTokensCard />);

    expect(await screen.findByText("CI runner")).toBeTruthy();
    expect(screen.getByText("readonly-scoped")).toBeTruthy();
    expect(screen.getByText("other-vault")).toBeTruthy();
    expect(screen.getByText("settings.security.apiTokens.scopeGlobal")).toBeTruthy();
    expect(screen.getByText("settings.security.apiTokens.accessReadOnly")).toBeTruthy();
    expect(screen.getByText("settings.security.apiTokens.accessReadWrite")).toBeTruthy();
    expect(screen.getByText("settings.security.apiTokens.neverUsed")).toBeTruthy();
  });

  it("shows a load error message when fetchApiTokens rejects", async () => {
    fetchApiTokens.mockRejectedValue(new Error("network down"));
    render(<ApiTokensCard />);

    expect(await screen.findByText("settings.security.apiTokens.loadError")).toBeTruthy();
  });
});

describe("ApiTokensCard — create flow (one-time reveal)", () => {
  it("creates a token, reveals the plaintext once, and reloads the list", async () => {
    fetchApiTokens
      .mockResolvedValueOnce({ tokens: [] })
      .mockResolvedValueOnce({
        tokens: [
          {
            id: "tok-new",
            label: "new-token",
            vault_id: null,
            read_only: false,
            created_at: "2026-07-17T00:00:00Z",
            last_used_at: null,
          },
        ],
      });
    createApiToken.mockResolvedValue({
      id: "tok-new",
      label: "new-token",
      vault_id: null,
      read_only: false,
      created_at: "2026-07-17T00:00:00Z",
      token: "plaintext-secret-abc123",
    });

    render(<ApiTokensCard />);
    await screen.findByText("settings.security.apiTokens.empty");

    fireEvent.change(screen.getByPlaceholderText("settings.security.apiTokens.labelPlaceholder"), {
      target: { value: "new-token" },
    });
    fireEvent.click(screen.getByTestId("api-token-create-btn"));

    await waitFor(() => expect(createApiToken).toHaveBeenCalledWith({
      label: "new-token",
      vault_id: null,
      read_only: false,
    }));

    const revealValue = await screen.findByTestId("api-token-reveal-value");
    expect(revealValue.textContent).toContain("plaintext-secret-abc123");
    expect(screen.getByText("settings.security.apiTokens.revealWarning")).toBeTruthy();

    // Dismissing hides the plaintext.
    fireEvent.click(screen.getByTestId("api-token-reveal-dismiss-btn"));
    expect(screen.queryByTestId("api-token-reveal-value")).toBeNull();

    // The list reloaded and now shows the created token.
    expect(await screen.findByText("new-token")).toBeTruthy();
  });

  it("sends a trimmed vault_id and read_only flag", async () => {
    fetchApiTokens.mockResolvedValue({ tokens: [] });
    createApiToken.mockResolvedValue({
      id: "tok-scoped",
      label: "scoped",
      vault_id: "vault-x",
      read_only: true,
      created_at: "2026-07-17T00:00:00Z",
      token: "plaintext-scoped-xyz",
    });

    render(<ApiTokensCard />);
    await screen.findByText("settings.security.apiTokens.empty");

    fireEvent.change(screen.getByPlaceholderText("settings.security.apiTokens.labelPlaceholder"), {
      target: { value: "scoped" },
    });
    fireEvent.change(
      screen.getByPlaceholderText("settings.security.apiTokens.vaultScopePlaceholder"),
      { target: { value: "  vault-x  " } },
    );
    fireEvent.click(screen.getByTestId("api-token-readonly-checkbox"));
    fireEvent.click(screen.getByTestId("api-token-create-btn"));

    await waitFor(() =>
      expect(createApiToken).toHaveBeenCalledWith({
        label: "scoped",
        vault_id: "vault-x",
        read_only: true,
      }),
    );
  });

  it("shows a create error message when createApiToken rejects", async () => {
    fetchApiTokens.mockResolvedValue({ tokens: [] });
    createApiToken.mockRejectedValue(new Error("boom"));

    render(<ApiTokensCard />);
    await screen.findByText("settings.security.apiTokens.empty");

    fireEvent.change(screen.getByPlaceholderText("settings.security.apiTokens.labelPlaceholder"), {
      target: { value: "fails" },
    });
    fireEvent.click(screen.getByTestId("api-token-create-btn"));

    expect(await screen.findByText("settings.security.apiTokens.createError")).toBeTruthy();
  });
});

describe("ApiTokensCard — revoke flow", () => {
  it("confirms, revokes, and reloads the list without the revoked token", async () => {
    fetchApiTokens
      .mockResolvedValueOnce({
        tokens: [
          {
            id: "tok-1",
            label: "to-revoke",
            vault_id: null,
            read_only: false,
            created_at: "2026-07-01T00:00:00Z",
            last_used_at: null,
          },
        ],
      })
      .mockResolvedValueOnce({ tokens: [] });
    revokeApiToken.mockResolvedValue(undefined);

    render(<ApiTokensCard />);
    await screen.findByText("to-revoke");

    fireEvent.click(screen.getByTestId("api-token-revoke-btn-tok-1"));

    await waitFor(() => expect(revokeApiToken).toHaveBeenCalledWith("tok-1"));
    expect(await screen.findByText("settings.security.apiTokens.empty")).toBeTruthy();
  });

  it("does not call revokeApiToken when the confirm dialog is dismissed", async () => {
    (window.confirm as ReturnType<typeof vi.fn>).mockReturnValue(false);
    fetchApiTokens.mockResolvedValue({
      tokens: [
        {
          id: "tok-1",
          label: "keep-me",
          vault_id: null,
          read_only: false,
          created_at: "2026-07-01T00:00:00Z",
          last_used_at: null,
        },
      ],
    });

    render(<ApiTokensCard />);
    await screen.findByText("keep-me");

    fireEvent.click(screen.getByTestId("api-token-revoke-btn-tok-1"));
    expect(revokeApiToken).not.toHaveBeenCalled();
  });
});
