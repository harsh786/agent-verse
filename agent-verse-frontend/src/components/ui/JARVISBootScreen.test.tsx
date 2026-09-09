import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, act } from '@testing-library/react';
import { JARVISBootScreen } from './JARVISBootScreen';

/**
 * Regression guard for a shipped outage: OrgPage (and any host) renders the boot
 * screen and gates ALL of its content on `onComplete`. The previous implementation
 * awaited a framer-motion `controls.start()` on a node that unmounts the instant
 * retraction begins, so the promise never resolved and `onComplete` never fired —
 * every AI-Organization detail page was permanently blank. `onComplete` must fire
 * exactly once, a bounded time after `duration`, regardless of animation behaviour.
 */
describe('JARVISBootScreen', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('calls onComplete exactly once after the duration elapses', () => {
    const onComplete = vi.fn();
    render(<JARVISBootScreen orgName="Acme" onComplete={onComplete} duration={1000} />);

    expect(onComplete).not.toHaveBeenCalled();

    // Phase 1: fire the duration timer (setVisible(false)) and let React commit
    // so the completion effect can schedule its timer.
    act(() => { vi.advanceTimersByTime(1000); });
    // Phase 2: fire the completion timer.
    act(() => { vi.advanceTimersByTime(800); });

    expect(onComplete).toHaveBeenCalledTimes(1);
  });

  it('does not call onComplete before the duration elapses', () => {
    const onComplete = vi.fn();
    render(<JARVISBootScreen orgName="Acme" onComplete={onComplete} duration={3200} />);
    act(() => { vi.advanceTimersByTime(1000); });
    expect(onComplete).not.toHaveBeenCalled();
  });

  it('does not fire onComplete twice even when the exit animation also completes', () => {
    const onComplete = vi.fn();
    render(<JARVISBootScreen orgName="Acme" onComplete={onComplete} duration={500} />);
    act(() => { vi.advanceTimersByTime(500); });
    act(() => { vi.advanceTimersByTime(5000); });
    expect(onComplete).toHaveBeenCalledTimes(1);
  });
});
