/**
 * Public product identity.
 *
 * Keep display copy separate from technical identifiers (`synapse.*`, package names,
 * bundle IDs) so a future rename does not require changing persistence contracts.
 */
export const PRODUCT_IDENTITY = Object.freeze({
  displayName: "Synapse",
  descriptor: "The self-hosted LLM wiki that turns your sources into connected knowledge.",
  tagline: "Connect everything.",
} as const);
