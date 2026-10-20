import { effect, Signal } from '@angular/core';

// At most 5 s may pass between checks (Req 10.12); 3 s leaves room for request latency.
export const POLL_INTERVAL_MS = 3000;

export function pollWhile(
  condition: Signal<boolean>,
  intervalMs: number,
  tick: () => Promise<void>,
): void {
  effect((onCleanup) => {
    if (!condition()) {
      return;
    }
    const timer = setInterval(() => void tick(), intervalMs);
    onCleanup(() => clearInterval(timer));
  });
}
