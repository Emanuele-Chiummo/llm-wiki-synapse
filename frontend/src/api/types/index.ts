/**
 * API contract types for Synapse frontend — barrel re-export (FE-QUAL-11).
 *
 * `api/types.ts` used to be a single ~1100-line file holding every domain's
 * types. Split into one file per domain (this directory); this index
 * re-exports everything so existing `import ... from "../api/types"` /
 * `from "./types"` call sites keep working unchanged.
 *
 * INVARIANT I2: coords (x, y) come FROM the server; the client NEVER computes layout.
 */

export * from "./graph";
export * from "./pages";
export * from "./status";
export * from "./ingest";
export * from "./provider";
export * from "./research";
export * from "./review";
export * from "./cascade";
export * from "./importSchedule";
export * from "./clipConfig";
export * from "./webSearch";
export * from "./lint";
export * from "./apiTokens";
