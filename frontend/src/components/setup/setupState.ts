export const SETUP_STATE_KEY = "synapse.setupState";
export const LEGACY_SETUP_COMPLETED_KEY = "synapse.setupCompleted";
export const SETUP_STATE_VERSION = 1 as const;

export type SetupStatus = "pending" | "deferred" | "completed";
export type SetupStep = 1 | 2 | 3 | 4;

export interface SetupState {
  version: typeof SETUP_STATE_VERSION;
  status: SetupStatus;
  lastStep: SetupStep;
  connectionVerified: boolean;
  providerVerified: boolean;
  /** Exact provider row/revision that passed the connection probe. */
  providerFingerprint: string | null;
  updatedAt: string;
}

export type SetupChecks = Pick<SetupState, "connectionVerified" | "providerVerified"> & {
  providerFingerprint?: string | null | undefined;
};

function defaultState(): SetupState {
  return {
    version: SETUP_STATE_VERSION,
    status: "pending",
    lastStep: 1,
    connectionVerified: false,
    providerVerified: false,
    providerFingerprint: null,
    updatedAt: new Date().toISOString(),
  };
}

function isSetupState(value: unknown): value is SetupState {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Partial<SetupState>;
  return (
    candidate.version === SETUP_STATE_VERSION &&
    (candidate.status === "pending" ||
      candidate.status === "deferred" ||
      candidate.status === "completed") &&
    (candidate.lastStep === 1 ||
      candidate.lastStep === 2 ||
      candidate.lastStep === 3 ||
      candidate.lastStep === 4) &&
    typeof candidate.updatedAt === "string"
  );
}

function persistSetupState(state: SetupState): SetupState {
  try {
    localStorage.setItem(SETUP_STATE_KEY, JSON.stringify(state));
    if (state.status === "completed") {
      localStorage.setItem(LEGACY_SETUP_COMPLETED_KEY, "1");
    }
  } catch {
    // Storage can be unavailable in hardened browsers. The caller still gets
    // a valid in-memory value for the current render.
  }
  return state;
}

export function readSetupState(): SetupState {
  try {
    const raw = localStorage.getItem(SETUP_STATE_KEY);
    if (raw !== null) {
      const parsed: unknown = JSON.parse(raw);
      if (isSetupState(parsed)) {
        const saved = parsed as SetupState;
        const completed = saved.status === "completed";
        return {
          ...saved,
          connectionVerified: completed || saved.connectionVerified === true,
          providerVerified: completed || saved.providerVerified === true,
          providerFingerprint:
            typeof saved.providerFingerprint === "string" ? saved.providerFingerprint : null,
        };
      }
    }

    if (localStorage.getItem(LEGACY_SETUP_COMPLETED_KEY) === "1") {
      return persistSetupState({
        version: SETUP_STATE_VERSION,
        status: "completed",
        lastStep: 4,
        connectionVerified: true,
        providerVerified: true,
        providerFingerprint: null,
        updatedAt: new Date().toISOString(),
      });
    }
  } catch {
    // Invalid JSON or unavailable storage is treated as a fresh setup.
  }
  return defaultState();
}

export function completeSetup(checks?: Partial<SetupChecks>): SetupState {
  const current = readSetupState();
  return persistSetupState({
    version: SETUP_STATE_VERSION,
    status: "completed",
    lastStep: 4,
    connectionVerified: true,
    providerVerified: true,
    providerFingerprint: checks?.providerFingerprint ?? current.providerFingerprint,
    updatedAt: new Date().toISOString(),
  });
}

export function deferSetup(lastStep: SetupStep, checks?: SetupChecks): SetupState {
  const current = readSetupState();
  if (current.status === "completed") return current;
  return persistSetupState({
    version: SETUP_STATE_VERSION,
    status: "deferred",
    lastStep,
    connectionVerified: checks?.connectionVerified ?? current.connectionVerified,
    providerVerified: checks?.providerVerified ?? current.providerVerified,
    providerFingerprint: checks?.providerFingerprint ?? current.providerFingerprint,
    updatedAt: new Date().toISOString(),
  });
}
