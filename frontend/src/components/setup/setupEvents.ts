import type { SetupStep } from "./setupState";

export const OPEN_SETUP_EVENT = "synapse:openWizard";

interface OpenSetupDetail {
  step?: SetupStep | undefined;
}

export function openSetupWizard(step?: SetupStep): void {
  window.dispatchEvent(new CustomEvent<OpenSetupDetail>(OPEN_SETUP_EVENT, { detail: { step } }));
}

export function requestedSetupStep(event: Event): SetupStep | undefined {
  const step = (event as CustomEvent<OpenSetupDetail>).detail?.step;
  return step === 1 || step === 2 || step === 3 || step === 4 ? step : undefined;
}
