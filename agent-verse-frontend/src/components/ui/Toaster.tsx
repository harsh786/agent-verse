import { useEffect, useRef } from "react";
import { CheckCircle2, AlertCircle, Info, AlertTriangle, X } from "lucide-react";
import { useToastStore } from "@/stores/toast";

const KIND_CONFIG = {
  success: {
    icon: CheckCircle2,
    className: "border-green-500/40 bg-green-50/80 dark:bg-green-950/40 text-green-800 dark:text-green-300",
    duration: 4000,
  },
  error: {
    icon: AlertCircle,
    className: "border-red-500/40 bg-red-50/80 dark:bg-red-950/40 text-red-800 dark:text-red-300",
    duration: 6000,
  },
  info: {
    icon: Info,
    className: "border-blue-500/40 bg-blue-50/80 dark:bg-blue-950/40 text-blue-800 dark:text-blue-300",
    duration: 4000,
  },
  warning: {
    icon: AlertTriangle,
    className: "border-yellow-500/40 bg-yellow-50/80 dark:bg-yellow-950/40 text-yellow-800 dark:text-yellow-300",
    duration: 0, // sticky
  },
} as const;

function ToastItem({ id, kind, message }: { id: string; kind: keyof typeof KIND_CONFIG; message: string }) {
  const dismiss = useToastStore((s) => s.dismiss);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const config = KIND_CONFIG[kind] ?? KIND_CONFIG.info;
  const Icon = config.icon;

  useEffect(() => {
    if (config.duration > 0) {
      timerRef.current = setTimeout(() => dismiss(id), config.duration);
    }
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [id, config.duration, dismiss]);

  return (
    <div
      role="status"
      aria-live="polite"
      className={`flex items-start gap-3 border rounded-lg shadow-lg px-4 py-3 text-sm max-w-sm
        animate-in slide-in-from-bottom-2 duration-300 ${config.className}`}
    >
      <Icon className="h-4 w-4 mt-0.5 shrink-0" aria-hidden="true" />
      <span className="flex-1 break-words">{message}</span>
      <button
        aria-label="Dismiss notification"
        onClick={() => dismiss(id)}
        className="text-current opacity-60 hover:opacity-100 transition-opacity shrink-0 ml-1"
      >
        <X className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}

export function Toaster() {
  const toasts = useToastStore((s) => s.toasts);
  return (
    <div
      className="fixed bottom-4 right-4 z-[100] flex flex-col-reverse gap-2 max-h-screen overflow-hidden pointer-events-none"
      role="region"
      aria-label="Notifications"
    >
      {toasts.map((t) => (
        <div key={t.id} className="pointer-events-auto">
          <ToastItem id={t.id} kind={t.kind as keyof typeof KIND_CONFIG} message={t.message} />
        </div>
      ))}
    </div>
  );
}
