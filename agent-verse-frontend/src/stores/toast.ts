import { create } from 'zustand';

export type ToastKind = 'success' | 'error' | 'info' | 'warning';
export interface ToastItem { id: string; kind: ToastKind; message: string }

interface ToastState {
  toasts: ToastItem[];
  toast: (t: { kind: ToastKind; message: string }) => string;
  dismiss: (id: string) => void;
}

let seq = 0;
const nextId = (): string => `t-${Date.now()}-${seq++}`;

export const useToastStore = create<ToastState>((set) => ({
  toasts: [],
  toast: ({ kind, message }) => {
    const id = nextId();
    set((s) => ({ toasts: [...s.toasts, { id, kind, message }] }));
    return id;
  },
  dismiss: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
}));

/** Imperative helper for non-React callers (e.g. the API client). */
export const toast = (t: { kind: ToastKind; message: string }): string =>
  useToastStore.getState().toast(t);
