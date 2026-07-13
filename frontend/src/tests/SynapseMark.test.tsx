/** @vitest-environment jsdom */

import { render } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../store/settingsStore", () => ({
  useSettingsStore: (selector: (state: { theme: string }) => unknown) =>
    selector({ theme: "light" }),
  selectTheme: (state: { theme: string }) => state.theme,
  resolveTheme: () => "light",
}));

import { SynapseMark } from "../components/brand/SynapseMark";

describe("SynapseMark responsive detail", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("uses the simplified, satellite-free mark below 24px", () => {
    const { container } = render(<SynapseMark size={22} />);

    const mark = container.querySelector("svg");
    expect(mark?.getAttribute("data-mark-detail")).toBe("simplified");
    expect(mark?.querySelector('[data-mark-part="satellites"]')).toBeNull();
  });

  it("preserves the master mark at 24px and above", () => {
    const { container } = render(<SynapseMark size={24} />);

    const mark = container.querySelector("svg");
    expect(mark?.getAttribute("data-mark-detail")).toBe("master");
    expect(mark?.querySelector('[data-mark-part="satellites"]')).not.toBeNull();
  });
});
