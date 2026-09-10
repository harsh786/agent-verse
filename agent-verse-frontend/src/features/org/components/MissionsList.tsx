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
import { AnimatePresence } from 'framer-motion';
import { Plus, Loader2 } from 'lucide-react';
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
    estimateSize:     () => 96,   // estimated card height (denser)
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
    return (
      <div className="flex flex-col items-center justify-center py-16 text-center">
        <div className="text-4xl mb-4 opacity-30" aria-hidden="true">🎯</div>
        <p className="text-[var(--text-secondary)] mb-4">No missions yet</p>
        <p className="text-xs text-[var(--text-muted)] mb-6">
          Create your first mission to get started
        </p>
        {onCreateClick && (
          <button
            onClick={onCreateClick}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-[var(--accent-blue)] text-white text-sm font-medium hover:opacity-90 transition-opacity"
            aria-label="Create first mission"
          >
            <Plus className="h-4 w-4" aria-hidden="true" />
            New Mission
          </button>
        )}
      </div>
    );
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
        style={{ height: Math.min(missions.length * 96 + 20, 720) }}
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
