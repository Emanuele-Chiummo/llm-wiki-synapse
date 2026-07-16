/** @vitest-environment jsdom */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

const fetchUpdateStatus = vi.fn();
const triggerSystemUpdate = vi.fn();
vi.mock("../api/opsClient", () => ({
  fetchUpdateStatus: (...args: unknown[]) => fetchUpdateStatus(...args),
  triggerSystemUpdate: (...args: unknown[]) => triggerSystemUpdate(...args),
}));

import { SectionAbout } from "../components/settings/sections/SectionAbout";
import { PRODUCT_IDENTITY } from "../config/productIdentity";

beforeEach(() => {
  fetchUpdateStatus.mockReset();
  triggerSystemUpdate.mockReset();
});

describe("SectionAbout product identity", () => {
  it("renders the centralized descriptor and tagline", () => {
    fetchUpdateStatus.mockRejectedValue(new Error("offline"));
    render(<SectionAbout />);

    expect(
      screen.getByText(`${PRODUCT_IDENTITY.displayName} — ${PRODUCT_IDENTITY.descriptor}`),
    ).toBeTruthy();
    expect(screen.getByText(PRODUCT_IDENTITY.tagline)).toBeTruthy();
  });
});

describe("SectionAbout update section (R12-3)", () => {
  it("shows the update button when an update is available AND supported", async () => {
    fetchUpdateStatus.mockResolvedValue({
      current_version: "1.7.1",
      latest_version: "1.7.2",
      update_available: true,
      update_supported: true,
    });
    render(<SectionAbout />);

    const btn = await screen.findByTestId("settings-update-button");
    expect(btn.textContent).toContain("settings.about.updateNow");
    expect(screen.getByText(/settings\.about\.updateAvailable/)).toBeTruthy();
  });

  it("hides the button (manual hint only) when available but NOT supported", async () => {
    fetchUpdateStatus.mockResolvedValue({
      current_version: "1.7.1",
      latest_version: "1.7.2",
      update_available: true,
      update_supported: false,
    });
    render(<SectionAbout />);

    expect(await screen.findByTestId("settings-update-status")).toBeTruthy();
    expect(screen.queryByTestId("settings-update-button")).toBeNull();
  });

  it("triggers the system update on click and shows the 'started' state", async () => {
    fetchUpdateStatus.mockResolvedValue({
      current_version: "1.7.1",
      latest_version: "1.7.2",
      update_available: true,
      update_supported: true,
    });
    triggerSystemUpdate.mockResolvedValue({ triggered: true, message: "ok" });
    render(<SectionAbout />);

    fireEvent.click(await screen.findByTestId("settings-update-button"));
    await waitFor(() => expect(triggerSystemUpdate).toHaveBeenCalledOnce());
    expect(await screen.findByText(/settings\.about\.updateStarted/)).toBeTruthy();
  });

  it("shows 'up to date' when running the latest version", async () => {
    fetchUpdateStatus.mockResolvedValue({
      current_version: "1.7.2",
      latest_version: "1.7.2",
      update_available: false,
      update_supported: true,
    });
    render(<SectionAbout />);

    expect(await screen.findByTestId("settings-update-uptodate")).toBeTruthy();
  });
});
