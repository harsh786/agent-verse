/**
 * Skeleton loading components for workflow pages.
 *
 * Uses Tailwind animate-pulse for skeleton shimmer effect.
 * WCAG: role="status" + aria-label="Loading..."
 */

function SkeletonBox({ className = '' }: { className?: string }) {
  return <div className={`animate-pulse rounded-lg bg-white/5 ${className}`} />;
}

// ── Workflow List Skeleton ────────────────────────────────────────────────────

export function WorkflowListSkeleton() {
  return (
    <div role="status" aria-label="Loading workflows…" className="space-y-4">
      {/* Search bar */}
      <div className="flex gap-3 mb-6">
        <SkeletonBox className="h-9 w-64 rounded-xl" />
        <SkeletonBox className="h-9 w-96 rounded-xl" />
      </div>
      {/* Card grid */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="rounded-2xl border border-white/8 p-5 space-y-3">
            <div className="flex items-start justify-between">
              <div className="space-y-2 flex-1">
                <SkeletonBox className="h-4 w-3/4" />
                <SkeletonBox className="h-3 w-full" />
                <SkeletonBox className="h-3 w-2/3" />
              </div>
              <SkeletonBox className="h-5 w-16 rounded-full ml-3" />
            </div>
            <SkeletonBox className="h-3 w-1/2" />
            <div className="flex gap-2 pt-2 border-t border-white/5">
              <SkeletonBox className="h-7 flex-1 rounded-lg" />
              <SkeletonBox className="h-7 flex-1 rounded-lg" />
              <SkeletonBox className="h-7 w-7 rounded-lg" />
            </div>
          </div>
        ))}
      </div>
      <span className="sr-only">Loading workflows…</span>
    </div>
  );
}

// ── Workflow Canvas Skeleton ───────────────────────────────────────────────────

export function WorkflowCanvasSkeleton() {
  return (
    <div
      role="status"
      aria-label="Loading workflow canvas…"
      className="flex h-full w-full"
    >
      {/* Palette */}
      <div className="w-52 shrink-0 border-r border-white/10 bg-slate-900/80 p-3 space-y-4">
        <SkeletonBox className="h-9 w-full rounded-xl" />
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="space-y-2">
            <SkeletonBox className="h-3 w-16" />
            {Array.from({ length: 3 }).map((_, j) => (
              <SkeletonBox key={j} className="h-8 w-full rounded-lg" />
            ))}
          </div>
        ))}
      </div>

      {/* Canvas area */}
      <div className="flex-1 relative bg-slate-950 overflow-hidden">
        {/* Dots background */}
        <div className="absolute inset-0 opacity-20"
          style={{ backgroundImage: 'radial-gradient(circle, #334155 1px, transparent 1px)', backgroundSize: '20px 20px' }} />

        {/* Mock nodes */}
        {[
          { x: '20%', y: '30%', w: '160px' },
          { x: '45%', y: '25%', w: '180px' },
          { x: '45%', y: '55%', w: '160px' },
          { x: '70%', y: '40%', w: '170px' },
        ].map((pos, i) => (
          <div
            key={i}
            className="absolute animate-pulse rounded-xl border border-white/10 bg-white/5 p-3"
            style={{ left: pos.x, top: pos.y, width: pos.w }}
          >
            <div className="h-3 w-2/3 bg-white/10 rounded mb-2" />
            <div className="h-2 w-1/2 bg-white/6 rounded" />
          </div>
        ))}
      </div>

      <span className="sr-only">Loading canvas…</span>
    </div>
  );
}

// ── Run Timeline Skeleton ─────────────────────────────────────────────────────

export function RunTimelineSkeleton() {
  return (
    <div role="status" aria-label="Loading run timeline…" className="space-y-1">
      {Array.from({ length: 5 }).map((_, i) => (
        <div key={i} className="flex gap-4 py-2">
          <SkeletonBox className="h-8 w-8 rounded-full shrink-0" />
          <div className="flex-1 space-y-2 py-1">
            <div className="flex items-center justify-between">
              <SkeletonBox className="h-3.5 w-32" />
              <SkeletonBox className="h-3 w-12" />
            </div>
            <SkeletonBox className="h-2.5 w-20" />
          </div>
        </div>
      ))}
      <span className="sr-only">Loading timeline…</span>
    </div>
  );
}

// ── Run Detail Stats Skeleton ─────────────────────────────────────────────────

export function RunStatsSkeleton() {
  return (
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-3" role="status" aria-label="Loading run stats…">
      {Array.from({ length: 4 }).map((_, i) => (
        <div key={i} className="rounded-xl border border-white/8 bg-white/3 px-4 py-3 flex items-center gap-3">
          <SkeletonBox className="h-8 w-8 rounded-full shrink-0" />
          <div className="space-y-1.5">
            <SkeletonBox className="h-2.5 w-16" />
            <SkeletonBox className="h-4 w-12" />
          </div>
        </div>
      ))}
    </div>
  );
}
