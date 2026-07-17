/**
 * errors.ts — stable error-envelope parsing (ADR-0086).
 *
 * Replaces the old providerClient.formatDetail test: the flattening of FastAPI's 422
 * `detail` array now happens on the BACKEND (it pre-joins field errors into
 * `error.message`), so the frontend only needs to read the envelope's `message`/`code`.
 */

import { describe, it, expect } from "vitest";
import {
  ApiError,
  checkResponse,
  errorCodeFromBody,
  errorMessageFromBody,
  parseErrorEnvelope,
} from "../api/errors";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const envelope = (over: Record<string, unknown> = {}) => ({
  error: { code: "not_found", message: "Page 42 not found", status: 404, details: null, ...over },
});

describe("parseErrorEnvelope / errorMessageFromBody / errorCodeFromBody", () => {
  it("extracts message and code from a well-formed envelope", () => {
    const body = envelope();
    expect(parseErrorEnvelope(body)).toMatchObject({ code: "not_found", status: 404 });
    expect(errorMessageFromBody(body)).toBe("Page 42 not found");
    expect(errorCodeFromBody(body)).toBe("not_found");
  });

  it("returns undefined for non-envelope bodies (legacy {detail} or arbitrary JSON)", () => {
    expect(errorMessageFromBody({ detail: "old shape" })).toBeUndefined();
    expect(errorCodeFromBody({ detail: "old shape" })).toBeUndefined();
    expect(parseErrorEnvelope({ detail: "old shape" })).toBeUndefined();
    expect(errorMessageFromBody(null)).toBeUndefined();
    expect(errorMessageFromBody("string")).toBeUndefined();
    expect(errorMessageFromBody({ error: "not-an-object" })).toBeUndefined();
  });

  it("tolerates a partial envelope (missing message or code)", () => {
    expect(errorMessageFromBody({ error: { code: "gone", status: 410 } })).toBeUndefined();
    expect(errorCodeFromBody({ error: { message: "x", status: 500 } })).toBeUndefined();
  });
});

describe("ApiError", () => {
  it("carries status, message, and the optional stable code", () => {
    const err = new ApiError(404, "404 Page 42 not found", "not_found");
    expect(err.status).toBe(404);
    expect(err.code).toBe("not_found");
    expect(err.message).toBe("404 Page 42 not found");
    expect(err.name).toBe("ApiError");
  });

  it("code is optional", () => {
    expect(new ApiError(500, "boom").code).toBeUndefined();
  });
});

describe("checkResponse", () => {
  it("does not throw on a 2xx response", async () => {
    await expect(checkResponse(jsonResponse(200, { ok: true }))).resolves.toBeUndefined();
  });

  it("throws ApiError with envelope message + code on a 4xx", async () => {
    const res = jsonResponse(404, envelope());
    await expect(checkResponse(res)).rejects.toMatchObject({
      status: 404,
      code: "not_found",
      message: "404 Page 42 not found",
    });
  });

  it("surfaces the pre-joined validation message for a 422 (code=validation_error)", async () => {
    const res = jsonResponse(
      422,
      envelope({ code: "validation_error", message: "count: field required", status: 422 }),
    );
    await expect(checkResponse(res)).rejects.toMatchObject({
      status: 422,
      code: "validation_error",
      message: "422 count: field required",
    });
  });

  it("falls back to statusText when the body is not JSON", async () => {
    const res = new Response("<html>oops</html>", {
      status: 503,
      statusText: "Service Unavailable",
    });
    await expect(checkResponse(res)).rejects.toMatchObject({
      status: 503,
      message: "503 Service Unavailable",
    });
  });
});
