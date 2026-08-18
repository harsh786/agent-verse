/**
 * Workflow Toast — stacked, auto-dismiss, progress bar, pause-on-hover.
 *
 * 6 types: success | error | warning | info | hitl | loading
 * Springs: bouncy enter, smooth exit
 * Accessibility: role="status" / role="alert", aria-live
 */
import { useEffect, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { CheckCircle, XCircle, AlertTriangle, Info, UserCheck, Loader2, X } from 'lucide-react';
import { toastEnter } from '../design/motion';

// ── Types ─────────────────────────────────────────────────────────────────────

export type ToastType = 'success' | 'error' | 'warning' | 'info' | 'hitl' | 'loading';

export interface ToastMessage {
  id: string;
  type: ToastType;
  title: string;
  description?: string;
  duration?: number; // ms — 0 = sticky
  action?: { label: string; onClick: () => void };
}

// ── Icon + color map ──────────────────────────────────────────────────────────

const TOAST_CONFIG: Record<ToastType, { icon: React.ReactNode; ring: string; track: string }> = {
  success: {
    icon: <CheckCircle className="h-5 w-5 text-emerald-400" />,
    ring: 'border-emerald-500/40 bg-emerald-950/80',
    track: 'bg-emerald-400',
  },
  error: {
    icon: <XCircle className="h-5 w-5 text-red-400" />,
    ring: 'border-red-500/40 bg-red-950/80',
    track: 'bg-red-400',
  },
  warning: {
    icon: <AlertTriangle className="h-5 w-5 text-amber-400" />,
    ring: 'border-amber-500/40 bg-amber-950/80',
    track: 'bg-amber-400',
  },
  info: {
    icon: <Info className="h-5 w-5 text-sky-400" />,
    ring: 'border-sky-500/40 bg-sky-950/80',
    track: 'bg-sky-400',
  },
  hitl: {
    icon: <UserCheck className="h-5 w-5 text-rose-400" />,
    ring: 'border-rose-500/40 bg-rose-950/80',
    track: 'bg-rose-400',
  },
  loading: {
    icon: <Loader2 className="h-5 w-5 text-sky-400 animate-spin" />,
    ring: 'border-sky-500/30 bg-sky-950/70',
    track: 'bg-sky-400',
  },
};

// ── Single toast item ─────────────────────────────────────────────────────────

function ToastItem({
  toast,
  onDismiss,
}: {
  toast: ToastMessage;
  onDismiss: (id: string) => void;
}) {
  const duration = toast.duration ?? 5000;
  const [paused, setPaused] = useState(false);
  const [progress, setProgress] = useState(100);
  const startRef = useRef<number>(Date.now());
  const rafRef = useRef<number>(0);

  useEffect(() => {
    if (duration === 0 || toast.type === 'loading') return;

    const tick = () => {
      if (paused) {
        startRef.current = Date.now() - (duration * (1 - progress / 100));
        rafRef.current = requestAnimationFrame(tick);
        return;
      }
      const elapsed = Date.now() - startRef.current;
      const pct = Math.max(0, 100 - (elapsed / duration) * 100);
      setProgress(pct);
      if (pct <= 0) {
        onDismiss(toast.id);
        return;
      }
      rafRef.current = requestAnimationFrame(tick);
    };

    rafRef.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(rafRef.current);
  }, [paused, duration, toast.id, onDismiss, progress, toast.type]);

  const cfg = TOAST_CONFIG[toast.type];

  return (
    <motion.div
      layout
      variants={toastEnter}
      initial="initial"
      animate="animate"
      exit="exit"
      role={toast.type === 'error' ? 'alert' : 'status'}
      aria-live={toast.type === 'error' ? 'assertive' : 'polite'}
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
      className={`
        relative w-80 rounded-xl border backdrop-blur-md shadow-xl overflow-hidden
        ${cfg.ring}
      `}
    >
      {/* Content */}
      <div className="flex items-start gap-3 px-4 pt-4 pb-3">
        <span className="mt-0.5 shrink-0">{cfg.icon}</span>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-semibold text-white leading-tight">{toast.title}</p>
          {toast.description && (
            <p className="text-xs text-white/60 mt-0.5 leading-snug">{toast.description}</p>
          )}
          {toast.action && (
            <button
              onClick={toast.action.onClick}
              className="mt-1.5 text-xs font-medium text-sky-400 hover:text-sky-300 transition-colors"
            >
              {toast.action.label}
            </button>
          )}
        </div>
        <button
          onClick={() => onDismiss(toast.id)}
          aria-label="Dismiss notification"
          className="shrink-0 text-white/30 hover:text-white/70 transition-colors"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      {/* Progress bar */}
      {duration > 0 && toast.type !== 'loading' && (
        <div className="h-0.5 w-full bg-[#0F1826]/10">
          <div
            className={`h-full transition-none ${cfg.track}`}
            style={{ width: `${progress}%` }}
          />
        </div>
      )}
    </motion.div>
  );
}

// ── Toast stack container ─────────────────────────────────────────────────────

export function WorkflowToastStack({ toasts, onDismiss }: {
  toasts: ToastMessage[];
  onDismiss: (id: string) => void;
}) {
  return (
    <div
      aria-label="Notifications"
      className="fixed bottom-6 right-6 z-[60] flex flex-col gap-2 items-end"
    >
      <AnimatePresence mode="popLayout" initial={false}>
        {toasts.map((t) => (
          <ToastItem key={t.id} toast={t} onDismiss={onDismiss} />
        ))}
      </AnimatePresence>
    </div>
  );
}

// ── Hook ──────────────────────────────────────────────────────────────────────

let _toastCount = 0;

export function useWorkflowToast() {
  const [toasts, setToasts] = useState<ToastMessage[]>([]);

  const push = (msg: Omit<ToastMessage, 'id'>): string => {
    const id = `wt-${++_toastCount}`;
    setToasts((prev) => [...prev.slice(-4), { ...msg, id }]); // max 5 visible
    return id;
  };

  const dismiss = (id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  };

  const success = (title: string, description?: string) =>
    push({ type: 'success', title, description });

  const error = (title: string, description?: string) =>
    push({ type: 'error', title, description, duration: 8000 });

  const warning = (title: string, description?: string) =>
    push({ type: 'warning', title, description });

  const info = (title: string, description?: string) =>
    push({ type: 'info', title, description });

  const loading = (title: string, description?: string) =>
    push({ type: 'loading', title, description, duration: 0 });

  const hitl = (title: string, description?: string) =>
    push({ type: 'hitl', title, description, duration: 0 });

  return { toasts, dismiss, push, success, error, warning, info, loading, hitl };
}
