import { create } from 'zustand';

export type ToastKind = 'success' | 'error' | 'info' | 'warning';

export interface ToastAction {
  label: string;
  onClick: () => void;
}

export interface ToastItem {
  id: string;
  kind: ToastKind;
  message: string;
  duration?: number;
  action?: ToastAction;
}

interface ToastState {
  toasts: ToastItem[];
  add: (t: ToastItem) => void;
  dismiss: (id: string) => void;
}

const MAX_TOASTS = 5;

export const useToastStore = create<ToastState>((set) => ({
  toasts: [],
  add: (item) =>
    set((s) => {
      // Cap at MAX_TOASTS — drop the oldest when at limit
      const toasts = s.toasts.length >= MAX_TOASTS
        ? s.toasts.slice(1)
        : s.toasts;
      return { toasts: [...toasts, item] };
    }),
  dismiss: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
}));

/** Imperative helper for non-React callers (e.g. the API client).
 *  Deduplicates: if a toast with the same kind+message already exists, it is not added again.
 */
export const toast = (opts: Omit<ToastItem, 'id'>): string => {
  const store = useToastStore.getState();
  // Deduplicate by kind + message
  const existing = store.toasts.find((t) => t.message === opts.message && t.kind === opts.kind);
  if (existing) return existing.id;
  const id = typeof crypto !== 'undefined' ? crypto.randomUUID() : `t-${Date.now()}-${Math.random()}`;
  store.add({ ...opts, id });
  return id;
};
