/**
 * usePollChain.ts — React hook wrapper around `createPollChain` (FE-ARCH-2).
 *
 * Gives components an IMPERATIVE `start()` / `stop()` pair backed by the shared
 * setTimeout-chain primitive (I3/I7: single chain, AbortController, bounded).
 * The underlying chain always reads the LATEST callbacks via a ref, so callers
 * never need to worry about stale closures — just pass fresh functions each
 * render, same as any other React callback.
 *
 * The chain is force-stopped on unmount.
 *
 * Usage:
 *   const poll = usePollChain({
 *     fetch: (signal) => getSomeStatus(signal),
 *     onResult: (status) => setStatus(status),
 *     intervalFor: (status) => (status.running ? 1500 : null), // null = stop
 *   });
 *   // later, e.g. in a click handler or a mount effect:
 *   poll.start();
 *   // ...
 *   poll.stop();
 */

import { useEffect, useMemo, useRef } from "react";
import {
  createPollChain,
  type PollChainOptions,
  type PollChainController,
} from "../store/pollChain";

export interface UsePollChainHandle {
  start: () => void;
  stop: () => void;
  isRunning: () => boolean;
}

export function usePollChain<T>(opts: PollChainOptions<T>): UsePollChainHandle {
  const optsRef = useRef(opts);
  optsRef.current = opts;

  const chainRef = useRef<PollChainController | null>(null);
  if (chainRef.current === null) {
    const initial = optsRef.current;
    chainRef.current = createPollChain<T>({
      fetch: (signal) => optsRef.current.fetch(signal),
      onResult: (result) => optsRef.current.onResult(result),
      intervalFor: (result) => optsRef.current.intervalFor(result),
      onError: (err) => optsRef.current.onError?.(err),
      errorIntervalFor: (err) => optsRef.current.errorIntervalFor?.(err),
      onGiveUp: () => optsRef.current.onGiveUp?.(),
      ...(initial.initialDelayMs !== undefined ? { initialDelayMs: initial.initialDelayMs } : {}),
      ...(initial.shouldContinue !== undefined
        ? { shouldContinue: () => optsRef.current.shouldContinue?.() ?? false }
        : {}),
    });
  }

  const unsubRef = useRef<(() => void) | null>(null);

  useEffect(() => {
    return () => {
      unsubRef.current?.();
      unsubRef.current = null;
      chainRef.current?.stop();
    };
  }, []);

  return useMemo<UsePollChainHandle>(
    () => ({
      start: () => {
        // Guard on isRunning(), not just "do we hold a detach fn" — a chain
        // that reached a terminal state (natural stop) must be restartable
        // even though the previous subscribe()'s detach closure is still around.
        if (chainRef.current?.isRunning()) return;
        unsubRef.current = chainRef.current?.subscribe() ?? null;
      },
      stop: () => {
        unsubRef.current?.();
        unsubRef.current = null;
      },
      isRunning: () => chainRef.current?.isRunning() ?? false,
    }),
    [],
  );
}
