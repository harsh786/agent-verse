import { create } from 'zustand';
import { persist } from 'zustand/middleware';

interface EmergencyState {
  isActive: boolean;
  activatedAt: string | null;
  cancelledGoals: number;
  rejectedApprovals: number;
  setActive: (stats: { cancelledGoals: number; rejectedApprovals: number }) => void;
  /** Apply the server's stop state (GET /governance/emergency-stop). */
  syncFromServer: (server: { active: boolean; activatedAt?: string | null }) => void;
  clear: () => void;
}

export const useEmergencyStore = create<EmergencyState>()(
  persist(
    (set) => ({
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
      syncFromServer: ({ active, activatedAt }) =>
        set((s) =>
          active
            ? { isActive: true, activatedAt: activatedAt ?? s.activatedAt ?? null }
            : { isActive: false, activatedAt: null, cancelledGoals: 0, rejectedApprovals: 0 }
        ),
      clear: () =>
        set({
          isActive: false,
          activatedAt: null,
          cancelledGoals: 0,
          rejectedApprovals: 0,
        }),
    }),
    { name: 'agentverse-emergency' }
  )
);
