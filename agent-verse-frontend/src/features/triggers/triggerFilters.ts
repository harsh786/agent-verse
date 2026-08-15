/**
 * Zustand store for trigger filter/view state.
 * Persists filter preferences in sessionStorage.
 */
import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';
import type { TriggerFamily } from './types';

type TriggerStatusFilter = 'all' | 'active' | 'paused';

interface TriggerFilterState {
  familyFilter: TriggerFamily | 'all';
  statusFilter: TriggerStatusFilter;
  searchQuery: string;
  // Actions
  setFamilyFilter: (family: TriggerFamily | 'all') => void;
  setStatusFilter: (status: TriggerStatusFilter) => void;
  setSearchQuery: (q: string) => void;
  resetFilters: () => void;
}

const DEFAULT: Pick<TriggerFilterState, 'familyFilter' | 'statusFilter' | 'searchQuery'> = {
  familyFilter: 'all',
  statusFilter: 'all',
  searchQuery: '',
};

export const useTriggerFilterStore = create<TriggerFilterState>()(
  persist(
    (set) => ({
      ...DEFAULT,
      setFamilyFilter: (family) => set({ familyFilter: family }),
      setStatusFilter: (status) => set({ statusFilter: status }),
      setSearchQuery: (q) => set({ searchQuery: q }),
      resetFilters: () => set(DEFAULT),
    }),
    {
      name: 'trigger-filters',
      storage: createJSONStorage(() => sessionStorage),
      partialize: (state) => ({
        familyFilter: state.familyFilter,
        statusFilter: state.statusFilter,
      }),
    },
  ),
);
