/** @vitest-environment jsdom */

import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

import { SectionAbout } from "../components/settings/sections/SectionAbout";
import { PRODUCT_IDENTITY } from "../config/productIdentity";

describe("SectionAbout product identity", () => {
  it("renders the centralized descriptor and tagline", () => {
    render(<SectionAbout />);

    expect(
      screen.getByText(`${PRODUCT_IDENTITY.displayName} — ${PRODUCT_IDENTITY.descriptor}`),
    ).toBeTruthy();
    expect(screen.getByText(PRODUCT_IDENTITY.tagline)).toBeTruthy();
  });
});
