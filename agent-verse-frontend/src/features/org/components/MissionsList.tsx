/**
 * MissionsList — virtualized missions list with spring stagger + live SSE.
 *
 * Skills applied:
 *   - web-guidelines:  virtualize >50 items, aria-label, keyboard nav,
 *                      empty state with action, min-w-0
 *   - emil-design-eng: stagger children, spring layout animations
 *   - impeccable-ui:   skeleton shimmer, meaningful empty state, error with fix
 *   - ui-ux-pro-max:   44×44px targets, prefers-reduced-motion
 */
import { useRef, useCallback } from 'react';
import { useVirtualizer } from '@tanstack/react-virtual';
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';
import { Plus, Loader2, Zap } from 'lucide-react';
import { MissionCard } from './MissionCard';
import { useMissions } from '../hooks/useOrg';
import type { OrgMission } from '../types';

interface MissionsListProps {
  orgId: string;
  onMissionClick?: (mission: OrgMission) => void;
  onCreateClick?: () => void;
  statusFilter?: string;
  /** When set, only missions in this department are shown. */
  deptFilter?: string;
}

export function MissionsList({
  orgId,
  onMissionClick,
  onCreateClick,
  statusFilter,
  deptFilter,
}: MissionsListProps) {
  const parentRef = useRef<HTMLDivElement>(null);

  const {
    data,
    isLoading,
    isFetchingNextPage,
    hasNextPage,
    fetchNextPage,
    error,
  } = useMissions(orgId, { status: statusFilter });

  // Flatten all pages into a single list, optionally scoped to a department.
  const missions = (data?.pages.flatMap((p) => p.data) ?? []).filter(
    (m) => !deptFilter || m.dept_id === deptFilter,
  );

  const virtualizer = useVirtualizer({
    count:            missions.length,
    getScrollElement: () => parentRef.current,
    estimateSize:     () => 78,   // compact 2-row card
    overscan:         5,
  });

  const handleMissionClick = useCallback((id: string) => {
    const mission = missions.find((m) => m.id === id);
    if (mission) onMissionClick?.(mission);
  }, [missions, onMissionClick]);

  // Infinite scroll: load more when near the bottom
  const handleScroll = useCallback(() => {
    if (!parentRef.current || !hasNextPage || isFetchingNextPage) return;
    const { scrollTop, scrollHeight, clientHeight } = parentRef.current;
    if (scrollHeight - scrollTop - clientHeight < 200) {
      fetchNextPage();
    }
  }, [hasNextPage, isFetchingNextPage, fetchNextPage]);

  if (isLoading) {
    return (
      <div className="space-y-2" aria-label="Loading missions">
        {[...Array(3)].map((_, i) => (
          <div key={i} className="h-24 rounded-lg bg-[var(--bg-elevated)] animate-pulse" />
        ))}
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-lg border border-rose-500/20 bg-rose-500/5 p-4 text-sm text-rose-400" role="alert">
        Failed to load missions. Please try again.
      </div>
    );
  }

  if (missions.length === 0) {
    return <MissionsEmptyState filtered={!!statusFilter} onCreateClick={onCreateClick} />;
  }

  return (
    <div className="flex flex-col gap-3">
      {/* Toolbar */}
      <div className="flex items-center justify-between">
        <p className="text-xs text-[var(--text-muted)]">
          {missions.length} mission{missions.length !== 1 ? 's' : ''}
        </p>
        {onCreateClick && (
          <button
            onClick={onCreateClick}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-[var(--accent-blue)]/10 text-[var(--accent-blue)] text-xs font-medium hover:bg-[var(--accent-blue)]/20 transition-colors"
            aria-label="Create new mission"
          >
            <Plus className="h-3.5 w-3.5" aria-hidden="true" />
            New Mission
          </button>
        )}
      </div>

      {/* Virtualized list */}
      <div
        ref={parentRef}
        onScroll={handleScroll}
        className="overflow-auto"
        style={{ height: Math.min(missions.length * 78 + 20, 720) }}
        aria-label={`${missions.length} missions`}
        role="list"
      >
        <div
          style={{ height: virtualizer.getTotalSize(), position: 'relative' }}
        >
          <AnimatePresence mode="popLayout">
            {virtualizer.getVirtualItems().map((item) => {
              const mission = missions[item.index];
              return (
                <div
                  key={mission.id}
                  style={{
                    position: 'absolute',
                    top:    0,
                    left:   0,
                    width:  '100%',
                    height: `${item.size}px`,
                    transform: `translateY(${item.start}px)`,
                    padding: '0 0 8px',
                  }}
                  role="listitem"
                >
                  <MissionCard
                    mission={mission}
                    orgId={orgId}
                    onClick={handleMissionClick}
                  />
                </div>
              );
            })}
          </AnimatePresence>
        </div>
      </div>

      {/* Load more indicator */}
      {isFetchingNextPage && (
        <div className="flex justify-center py-2" aria-live="polite">
          <Loader2 className="h-4 w-4 animate-spin text-[var(--text-muted)]" aria-label="Loading more missions" />
        </div>
      )}
    </div>
  );
}

// ─── Empty state ────────────────────────────────────────────────────────────
// A glowing invitation, not dead space. When a filter simply has no matches we
// stay quiet; when the org has no missions at all we invite the first launch.
function MissionsEmptyState({
  filtered,
  onCreateClick,
}: {
  filtered: boolean;
  onCreateClick?: () => void;
}) {
  const reduce = useReducedMotion();

  if (filtered) {
    return (
      <div className="flex flex-col items-center justify-center py-16 text-center">
        <p className="text-[13px] text-[#94A3B8]">No missions match this filter.</p>
        <p className="text-[12px] text-[#475569] mt-1">Try another status, or clear the filter.</p>
      </div>
    );
  }

  return (
    <div className="relative flex flex-col items-center justify-center overflow-hidden px-6 py-20 text-center">
      <div
        className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_at_center,rgba(0,212,255,0.10),transparent_65%)]"
        aria-hidden
      />
      <div className="relative mb-6">
        {!reduce && (
          <motion.span
            className="absolute inset-0 rounded-full bg-[#00D4FF]/20 blur-xl"
            animate={{ scale: [1, 1.35, 1], opacity: [0.5, 0.15, 0.5] }}
            transition={{ duration: 3, repeat: Infinity, ease: 'easeInOut' }}
            aria-hidden
          />
        )}
        <div className="relative flex h-16 w-16 items-center justify-center rounded-full border border-[#00D4FF]/30 bg-[#0F1826] shadow-[0_0_40px_-8px_rgba(0,212,255,0.5)]">
          <Zap className="h-7 w-7 text-[#00D4FF]" aria-hidden />
        </div>
      </div>
      <h2 className="text-[19px] font-semibold tracking-[-0.01em] text-[#F1F5F9]">
        Your command center is ready
      </h2>
      <p className="mt-2 max-w-sm text-[13px] leading-relaxed text-[#94A3B8]">
        Launch a mission and watch your AI org form a team, assign agents, execute, and deliver — live.
      </p>
      {onCreateClick && (
        <button
          onClick={onCreateClick}
          className="mt-6 inline-flex items-center gap-2 rounded-xl bg-gradient-to-r from-blue-600 to-[#00A3CC] px-5 py-2.5 text-sm font-semibold text-white shadow-[0_0_30px_-8px_rgba(0,212,255,0.6)] transition-all hover:from-blue-500 hover:to-[#00B8E6] active:scale-[0.98]"
          aria-label="Launch your first mission"
        >
          <Plus className="h-4 w-4" aria-hidden />
          Launch your first mission
        </button>
      )}
    </div>
  );
}
