import { useEffect, useRef, useState } from "react";
import { motion, AnimatePresence, useReducedMotion } from "framer-motion";
import { X } from "lucide-react";
import { useToastStore } from "@/stores/toast";
import type { ToastItem } from "@/stores/toast";
import { StatusOrb } from "./StatusOrb";

const KIND_CONFIG = {
  success: { borderColor: '#10B981', orbStatus: 'completed', duration: 4000 },
  error:   { borderColor: '#EF4444', orbStatus: 'failed',    duration: 6000 },
  info:    { borderColor: '#00D4FF', orbStatus: 'idle',       duration: 4000 },
  warning: { borderColor: '#F59E0B', orbStatus: 'pending',   duration: 0 },
} as const;

const SPRING = { type: 'spring', stiffness: 450, damping: 18 } as const;

function ToastItemView({ id, kind, message, duration, action, paused }: ToastItem & { paused?: boolean }) {
  const dismiss = useToastStore((s) => s.dismiss);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const config = KIND_CONFIG[kind] ?? KIND_CONFIG.info;
  const effectiveDuration = duration !== undefined ? duration : config.duration;

  useEffect(() => {
    if (effectiveDuration > 0 && !paused) {
      timerRef.current = setTimeout(() => dismiss(id), effectiveDuration);
    }
    return () => { if (timerRef.current) clearTimeout(timerRef.current); };
  }, [id, effectiveDuration, dismiss, paused]);

  return (
    <div
      className="flex items-center gap-3 px-4 py-3 rounded-xl min-w-[280px] max-w-[420px]
                 bg-[#1A1F2E] border border-white/[0.08] shadow-[0_8px_32px_rgba(0,0,0,0.5)]
                 cursor-pointer hover:border-white/[0.14] transition-colors"
      style={{ borderLeft: `3px solid ${config.borderColor}` }}
      onClick={() => dismiss(id)}
      role="status"
    >
      <StatusOrb status={config.orbStatus} size={8} className="shrink-0" />
      <p className="text-sm text-[#F1F5F9] flex-1 leading-snug break-words">{message}</p>
      {action && (
        <button
          onClick={(e) => { e.stopPropagation(); action.onClick(); dismiss(id); }}
          className="text-xs text-[#00D4FF] hover:text-white transition-colors shrink-0 font-medium ml-1"
        >
          {action.label}
        </button>
      )}
      <button
        aria-label="Dismiss notification"
        onClick={(e) => { e.stopPropagation(); dismiss(id); }}
        className="text-[#475569] hover:text-[#94A3B8] transition-colors shrink-0 ml-1"
      >
        <X className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}

export function Toaster() {
  const toasts = useToastStore((s) => s.toasts);
  const [hovering, setHovering] = useState(false);
  const reduce = useReducedMotion();

  return (
    <div
      className="fixed bottom-4 right-4 z-[200] flex flex-col gap-2 items-end pointer-events-none"
      role="region"
      aria-label="Notifications"
      aria-live="polite"
      aria-atomic="false"
      onMouseEnter={() => setHovering(true)}
      onMouseLeave={() => setHovering(false)}
    >
      <AnimatePresence mode="popLayout">
        {toasts.map((t) => (
          <motion.div
            key={t.id}
            layout
            initial={reduce ? { opacity: 1 } : { opacity: 1, y: 32, scale: 0.92 }}
            animate={reduce ? { opacity: 1 } : { opacity: 1, y: 0,  scale: 1 }}
            exit={reduce  ? { opacity: 0 } : { opacity: 0, y: 12, scale: 0.96 }}
            transition={reduce ? { duration: 0.15 } : SPRING}
            className="pointer-events-auto"
          >
            <ToastItemView {...t} paused={hovering} />
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  );
}
