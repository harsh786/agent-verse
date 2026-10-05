import { create } from 'zustand';

/**
 * Emergency-stop state for the banners. NOT persisted: the server
 * (GET /governance/emergency-stop) is the source of truth, and a remembered
 * browser-local value used to outlive the real stop (or show a stale count
 * when the status read failed).
 */
interface EmergencyState {
  isActive: boolean;
  activatedAt: string | null;
  cancelledGoals: number;
  rejectedApprovals: number;
  setActive: (stats: { cancelledGoals: number; rejectedApprovals: number }) => void;
  /** Apply the server's stop state (GET /governance/emergency-stop). */
  syncFromServer: (server: {
    active: boolean;
    activatedAt?: string | null;
    cancelledGoals?: number | null;
    rejectedApprovals?: number | null;
  }) => void;
  clear: () => void;
}

export const useEmergencyStore = create<EmergencyState>()((set) => ({
  isActive: false,
  activatedAt: null,
  cancelledGoals: 0,
  rejectedApprovals: 0,
  setActive: (stats) =>
    set({
      isActive: true,
      activatedAt: new Date().toISOString(),
      cancelledGoals: stats.cancelledGoals,
      rejectedApprovals: stats.rejectedApprovals,
    }),
  syncFromServer: ({ active, activatedAt, cancelledGoals, rejectedApprovals }) =>
    set((s) =>
      active
        ? {
            isActive: true,
            activatedAt: activatedAt ?? s.activatedAt ?? null,
            cancelledGoals: cancelledGoals ?? s.cancelledGoals,
            rejectedApprovals: rejectedApprovals ?? s.rejectedApprovals,
          }
        : { isActive: false, activatedAt: null, cancelledGoals: 0, rejectedApprovals: 0 }
    ),
  clear: () =>
    set({
      isActive: false,
      activatedAt: null,
      cancelledGoals: 0,
      rejectedApprovals: 0,
    }),
}));
