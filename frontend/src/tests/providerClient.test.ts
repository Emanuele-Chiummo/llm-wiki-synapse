/**
 * providerClient.formatDetail — FastAPI error `detail` normalisation (v1.5.1).
 *
 * Regression: FastAPI returns `detail` as a plain string for HTTPException but as an ARRAY of
 * {loc, msg, type} objects for 422 request-validation errors. The old code assigned that array
 * straight into a template string → "422 [object Object]" in the UI. formatDetail must turn the
 * array into a readable "field: message" list.
 */

import { describe, it, expect } from "vitest";
import { formatDetail } from "../api/providerClient";

describe("formatDetail", () => {
  it("passes a plain string through unchanged (HTTPException detail)", () => {
    expect(formatDetail("provide a config_id, or inline provider_type + model")).toBe(
      "provide a config_id, or inline provider_type + model",
    );
  });

  it("renders a FastAPI 422 validation array as 'field: message' (no [object Object])", () => {
    const detail = [
      { loc: ["body", "operation"], msg: "operation must be one of ...", type: "value_error" },
    ];
    const out = formatDetail(detail);
    expect(out).toBe("operation: operation must be one of ...");
    expect(out).not.toContain("[object Object]");
  });

  it("joins multiple validation errors with '; '", () => {
    const detail = [
      { loc: ["body", "model_id"], msg: "field required", type: "missing" },
      { loc: ["body", "scope"], msg: "invalid scope", type: "value_error" },
    ];
    expect(formatDetail(detail)).toBe("model_id: field required; scope: invalid scope");
  });

  it("returns undefined for shapes it cannot format (caller keeps its fallback)", () => {
    expect(formatDetail(undefined)).toBeUndefined();
    expect(formatDetail(null)).toBeUndefined();
    expect(formatDetail({ nope: true })).toBeUndefined();
    expect(formatDetail([])).toBeUndefined();
  });
});
