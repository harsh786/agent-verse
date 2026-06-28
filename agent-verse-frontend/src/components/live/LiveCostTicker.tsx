/**
 * LiveCostTicker — animated real-time cost counter.
 *
 * Shows a ticking cost display while a goal is executing.
 * Interpolates between cost snapshots using requestAnimationFrame.
 *
 * Usage:
 *   <LiveCostTicker currentCost={goal.cost_usd ?? 0} isRunning={goal.status === 'executing'} />
 */
import { useEffect, useRef, useState } from "react";
import { TrendingUp } from "lucide-react";

interface LiveCostTickerProps {
  /** Current known cost from the last event */
  currentCost: number;
  /** Whether the goal is actively running */
  isRunning: boolean;
  /** Estimated cost per second (used to interpolate) */
  estimatedRatePerSecond?: number;
  className?: string;
}

export function LiveCostTicker({
  currentCost,
  isRunning,
  estimatedRatePerSecond = 0.0002,
  className = "",
}: LiveCostTickerProps) {
  const [displayCost, setDisplayCost] = useState(currentCost);
  const rafRef = useRef<number | null>(null);
  const lastTickRef = useRef<number>(Date.now());
  const baseRef = useRef<number>(currentCost);

  // Sync base when known cost changes
  useEffect(() => {
    baseRef.current = currentCost;
    setDisplayCost(currentCost);
    lastTickRef.current = Date.now();
  }, [currentCost]);

  // Animate ticker while running
  useEffect(() => {
    if (!isRunning) {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
      return;
    }

    function tick() {
      const now = Date.now();
      const elapsed = (now - lastTickRef.current) / 1000;
      const interpolated = baseRef.current + elapsed * estimatedRatePerSecond;
      setDisplayCost(interpolated);
      rafRef.current = requestAnimationFrame(tick);
    }

    rafRef.current = requestAnimationFrame(tick);
    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
    };
  }, [isRunning, estimatedRatePerSecond]);

  const formatted =
    displayCost >= 0.01
      ? `$${displayCost.toFixed(3)}`
      : `$${displayCost.toFixed(5)}`;

  return (
    <span
      className={`inline-flex items-center gap-1 font-mono text-sm tabular-nums ${
        isRunning
          ? "text-amber-600 dark:text-amber-400"
          : "text-muted-foreground"
      } ${className}`}
      aria-label={`Cost: ${formatted}${isRunning ? " (running)" : ""}`}
      aria-live="polite"
      aria-atomic="true"
    >
      <TrendingUp
        className={`h-3.5 w-3.5 ${isRunning ? "animate-pulse" : ""}`}
        aria-hidden="true"
      />
      {formatted}
    </span>
  );
}
