import { create } from 'zustand';
import { persist } from 'zustand/middleware';

interface EmergencyState {
  isActive: boolean;
  activatedAt: string | null;
  cancelledGoals: number;
  rejectedApprovals: number;
  setActive: (stats: { cancelledGoals: number; rejectedApprovals: number }) => void;
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
