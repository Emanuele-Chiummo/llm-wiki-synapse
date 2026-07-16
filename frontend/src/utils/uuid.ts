/**
 * safeRandomUUID — a UUID v4 generator that also works in NON-secure contexts.
 *
 * `crypto.randomUUID()` is only exposed in a *secure context* (HTTPS or
 * `localhost`). When Synapse is served over `http://<lan-ip>` — e.g. a self-hosted
 * deployment reached at http://<lan-ip>:5173 — the page is NOT a secure
 * context, so `crypto.randomUUID` is `undefined` and calling it throws
 * "crypto.randomUUID is not a function", crashing the chat.
 *
 * `crypto.getRandomValues()` (unlike `randomUUID`/`crypto.subtle`) IS available
 * in insecure contexts, so we use it to build an RFC 4122 v4 UUID; if even that
 * is missing we degrade to Math.random (non-crypto, fine for a client-side id).
 */
export function safeRandomUUID(): string {
  const c: Crypto | undefined = globalThis.crypto;
  if (c && typeof c.randomUUID === "function") {
    return c.randomUUID();
  }
  const bytes = new Uint8Array(16);
  if (c && typeof c.getRandomValues === "function") {
    c.getRandomValues(bytes);
  } else {
    for (let i = 0; i < 16; i += 1) bytes[i] = Math.floor(Math.random() * 256);
  }
  // Set the RFC 4122 version (4) and variant (10xx) bits.
  // (?? 0 satisfies noUncheckedIndexedAccess — indices 6/8 always exist.)
  bytes[6] = ((bytes[6] ?? 0) & 0x0f) | 0x40;
  bytes[8] = ((bytes[8] ?? 0) & 0x3f) | 0x80;
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}
