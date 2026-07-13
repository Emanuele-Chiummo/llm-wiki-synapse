import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";

import { BackendConnectionBanner } from "../components/common/BackendConnectionBanner";
import { useStatusStore } from "../store/statusStore";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => key,
  }),
}));

describe("BackendConnectionBanner", () => {
  beforeEach(() => {
    useStatusStore.setState({ connectionState: "checking" });
  });

  it("stays hidden while checking or connected", () => {
    const { rerender } = render(<BackendConnectionBanner />);
    expect(screen.queryByTestId("backend-connection-banner")).toBeNull();

    act(() => useStatusStore.setState({ connectionState: "online" }));
    rerender(<BackendConnectionBanner />);
    expect(screen.queryByTestId("backend-connection-banner")).toBeNull();
  });

  it("explains the offline state and opens the guided connection check", () => {
    useStatusStore.setState({ connectionState: "offline" });
    const openWizard = vi.fn((event: Event) => event);
    window.addEventListener("synapse:openWizard", openWizard, { once: true });

    render(<BackendConnectionBanner />);

    expect(screen.getByTestId("backend-connection-banner")).not.toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "connection.checkSetup" }));
    expect(openWizard).toHaveBeenCalledOnce();
    expect((openWizard.mock.calls[0]?.[0] as CustomEvent).detail).toEqual({ step: 1 });
  });
});
