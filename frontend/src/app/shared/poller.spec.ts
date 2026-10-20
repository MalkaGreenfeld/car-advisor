import {
  createEnvironmentInjector,
  EnvironmentInjector,
  runInInjectionContext,
  Signal,
  signal,
} from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { POLL_INTERVAL_MS, pollWhile } from './poller';

describe('pollWhile', () => {
  const tick = vi.fn(() => Promise.resolve());
  let owner: EnvironmentInjector;

  function startPolling(condition: Signal<boolean>): void {
    runInInjectionContext(owner, () => pollWhile(condition, POLL_INTERVAL_MS, tick));
    TestBed.tick();
  }

  beforeEach(() => {
    vi.useFakeTimers();
    tick.mockClear();
    owner = createEnvironmentInjector([], TestBed.inject(EnvironmentInjector));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('ticks every 3 s while the condition holds', () => {
    startPolling(signal(true));

    vi.advanceTimersByTime(POLL_INTERVAL_MS - 1);
    expect(tick).not.toHaveBeenCalled();

    vi.advanceTimersByTime(1);
    expect(tick).toHaveBeenCalledTimes(1);

    vi.advanceTimersByTime(2 * POLL_INTERVAL_MS);
    expect(tick).toHaveBeenCalledTimes(3);
  });

  it('does not tick while the condition is false, and starts when it turns true', () => {
    const condition = signal(false);
    startPolling(condition);

    vi.advanceTimersByTime(2 * POLL_INTERVAL_MS);
    expect(tick).not.toHaveBeenCalled();

    condition.set(true);
    TestBed.tick();
    vi.advanceTimersByTime(2 * POLL_INTERVAL_MS);
    expect(tick).toHaveBeenCalledTimes(2);
  });

  it('stops when the condition turns false', () => {
    const condition = signal(true);
    startPolling(condition);
    vi.advanceTimersByTime(POLL_INTERVAL_MS);
    expect(tick).toHaveBeenCalledTimes(1);

    condition.set(false);
    TestBed.tick();
    vi.advanceTimersByTime(3 * POLL_INTERVAL_MS);

    expect(tick).toHaveBeenCalledTimes(1);
  });

  it('stops when its injector is destroyed', () => {
    startPolling(signal(true));
    vi.advanceTimersByTime(POLL_INTERVAL_MS);
    expect(tick).toHaveBeenCalledTimes(1);

    owner.destroy();
    vi.advanceTimersByTime(3 * POLL_INTERVAL_MS);

    expect(tick).toHaveBeenCalledTimes(1);
  });
});
