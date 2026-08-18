/**
 * ConfirmModal — accessible confirmation dialog for destructive actions.
 *
 * Usage:
 *   <ConfirmModal
 *     open={open}
 *     onConfirm={handleDelete}
 *     onCancel={() => setOpen(false)}
 *     title="Delete agent?"
 *     description="This cannot be undone."
 *     confirmLabel="Delete"
 *     variant="danger"
 *   />
 */
import { useEffect, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { AlertTriangle, Trash2, X } from "lucide-react";
import { SPRING_PAGE, JARVISButton } from "@/components/ui/JARVISPageShell";

const BACKDROP = { hidden: { opacity: 0 }, visible: { opacity: 1, transition: { duration: 0.18 } }, exit: { opacity: 0, transition: { duration: 0.14 } } };
const PANEL = { hidden: { opacity: 0, scale: 0.93, y: 12 }, visible: { opacity: 1, scale: 1, y: 0, transition: SPRING_PAGE }, exit: { opacity: 0, scale: 0.96, y: 8, transition: { duration: 0.14 } } };

interface ConfirmModalProps {
  open: boolean;
  title: string;
  description?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  variant?: "danger" | "warning" | "info";
  isLoading?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

const VARIANT_STYLES = {
  danger: {
    icon: Trash2,
    iconBg: "bg-red-100 dark:bg-red-900/30",
    iconColor: "text-red-600 dark:text-red-400",
    confirmBtn: "bg-red-600 hover:bg-red-700 text-white",
  },
  warning: {
    icon: AlertTriangle,
    iconBg: "bg-yellow-100 dark:bg-yellow-900/30",
    iconColor: "text-yellow-600 dark:text-yellow-400",
    confirmBtn: "bg-yellow-600 hover:bg-yellow-700 text-white",
  },
  info: {
    icon: AlertTriangle,
    iconBg: "bg-blue-100 dark:bg-blue-900/30",
    iconColor: "text-blue-600 dark:text-blue-400",
    confirmBtn: "bg-primary hover:opacity-90 text-primary-foreground",
  },
};

export function ConfirmModal({
  open,
  title,
  description,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  variant = "danger",
  isLoading = false,
  onConfirm,
  onCancel,
}: ConfirmModalProps) {
  const cancelRef = useRef<HTMLButtonElement>(null);
  const style = VARIANT_STYLES[variant];
  const Icon = style.icon;

  // Focus the cancel button when modal opens (safer default focus)
  useEffect(() => {
    if (open) {
      setTimeout(() => cancelRef.current?.focus(), 50);
    }
  }, [open]);

  // Close on Escape
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancel();
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [open, onCancel]);

  if (!open) return null;

  return (
    <AnimatePresence>
      {open && (
        <div
          className="fixed inset-0 z-[200] flex items-center justify-center p-4"
          role="dialog"
          aria-modal="true"
          aria-labelledby="confirm-modal-title"
          aria-describedby={description ? "confirm-modal-desc" : undefined}
        >
          {/* Backdrop */}
          <motion.div
            key="confirm-backdrop"
            variants={BACKDROP}
            initial="hidden" animate="visible" exit="exit"
            className="absolute inset-0 bg-black/60 backdrop-blur-sm"
            onClick={onCancel}
            aria-hidden="true"
          />

          {/* Panel */}
          <motion.div
            key="confirm-panel"
            variants={PANEL}
            initial="hidden" animate="visible" exit="exit"
            className="relative bg-[#1A1F2E] border border-white/[0.08] rounded-xl shadow-[0_20px_60px_rgba(0,0,0,0.6)] max-w-md w-full p-6"
          >
            <button
              onClick={onCancel}
              className="absolute top-4 right-4 text-[#475569] hover:text-[#94A3B8] transition-colors"
              aria-label="Close dialog"
            >
              <X className="h-4 w-4" />
            </button>

            <div className="flex items-start gap-4">
              <div className={`shrink-0 rounded-full p-2.5 ${style.iconBg}`}>
                <Icon className={`h-5 w-5 ${style.iconColor}`} aria-hidden="true" />
              </div>
              <div className="flex-1 min-w-0">
                <h2 id="confirm-modal-title" className="text-base font-semibold text-[#F1F5F9]">
                  {title}
                </h2>
                {description && (
                  <p id="confirm-modal-desc" className="mt-1 text-sm text-[#94A3B8]">
                    {description}
                  </p>
                )}
              </div>
            </div>

            <div className="mt-6 flex justify-end gap-3">
              <JARVISButton
                onClick={onCancel}
                disabled={isLoading}
                className="px-4 py-2 text-sm font-medium rounded-lg border border-white/[0.08] bg-white/[0.04] hover:bg-white/[0.08] text-[#94A3B8] hover:text-[#F1F5F9] transition-colors disabled:opacity-50"
              >
                {cancelLabel}
              </JARVISButton>
              <JARVISButton
                onClick={onConfirm}
                disabled={isLoading}
                className={`px-4 py-2 text-sm font-medium rounded-lg transition-colors disabled:opacity-50 ${style.confirmBtn}`}
              >
                {isLoading ? "Processing…" : confirmLabel}
              </JARVISButton>
            </div>
          </motion.div>
        </div>
      )}
    </AnimatePresence>
  );
}
