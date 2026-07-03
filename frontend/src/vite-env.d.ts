/// <reference types="vite/client" />

/**
 * Ambient declarations for Vite define() replacements.
 * __DEV__ is injected by vite.config.ts: true in dev, false in prod.
 */
declare const __DEV__: boolean;

/** App version injected from package.json by vite.config.ts define. */
declare const __APP_VERSION__: string;

interface ImportMetaEnv {
  /** Base URL of the Synapse FastAPI backend (no trailing slash). Default: http://localhost:8000 */
  readonly VITE_API_BASE?: string;
  /** Default vault ID to load on startup (optional) */
  readonly VITE_DEFAULT_VAULT_ID?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
