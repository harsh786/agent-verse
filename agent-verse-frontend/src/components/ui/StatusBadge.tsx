import { clsx } from "clsx";

type StatusValue =
  | "planning" | "executing" | "verifying" | "complete" | "completed"
  | "failed" | "cancelled" | "waiting_human" | "paused" | "pending"
  | "approved" | "rejected" | "active" | "inactive" | "draft"
  | "running" | "error" | "timeout" | "dry_run";

const STATUS_MAP: Record<string, { label: string; className: string }> = {
  planning:      { label: "Planning",    className: "bg-blue-100  text-blue-800  dark:bg-blue-900/40  dark:text-blue-300  border-blue-200  dark:border-blue-800"   },
  executing:     { label: "Executing",   className: "bg-violet-100 text-violet-800 dark:bg-violet-900/40 dark:text-violet-300 border-violet-200 dark:border-violet-800" },
  verifying:     { label: "Verifying",   className: "bg-cyan-100  text-cyan-800   dark:bg-cyan-900/40  dark:text-cyan-300   border-cyan-200  dark:border-cyan-800"   },
  complete:      { label: "Complete",    className: "bg-green-100 text-green-800  dark:bg-green-900/40 dark:text-green-300  border-green-200 dark:border-green-800"  },
  completed:     { label: "Complete",    className: "bg-green-100 text-green-800  dark:bg-green-900/40 dark:text-green-300  border-green-200 dark:border-green-800"  },
  failed:        { label: "Failed",      className: "bg-red-100   text-red-800    dark:bg-red-900/40   dark:text-red-300    border-red-200   dark:border-red-800"    },
  error:         { label: "Error",       className: "bg-red-100   text-red-800    dark:bg-red-900/40   dark:text-red-300    border-red-200   dark:border-red-800"    },
  cancelled:     { label: "Cancelled",   className: "bg-slate-100 text-slate-700  dark:bg-slate-900/40 dark:text-slate-300  border-slate-200 dark:border-slate-700"  },
  waiting_human: { label: "Awaiting OK", className: "bg-orange-100 text-orange-800 dark:bg-orange-900/40 dark:text-orange-300 border-orange-200 dark:border-orange-800" },
  paused:        { label: "Paused",      className: "bg-yellow-100 text-yellow-800 dark:bg-yellow-900/40 dark:text-yellow-300 border-yellow-200 dark:border-yellow-800" },
  pending:       { label: "Pending",     className: "bg-amber-100  text-amber-800  dark:bg-amber-900/40  dark:text-amber-300  border-amber-200  dark:border-amber-800"  },
  approved:      { label: "Approved",    className: "bg-green-100 text-green-800  dark:bg-green-900/40 dark:text-green-300  border-green-200 dark:border-green-800"  },
  rejected:      { label: "Rejected",    className: "bg-red-100   text-red-800    dark:bg-red-900/40   dark:text-red-300    border-red-200   dark:border-red-800"    },
  active:        { label: "Active",      className: "bg-green-100 text-green-800  dark:bg-green-900/40 dark:text-green-300  border-green-200 dark:border-green-800"  },
  inactive:      { label: "Inactive",    className: "bg-slate-100 text-slate-700  dark:bg-slate-900/40 dark:text-slate-300  border-slate-200 dark:border-slate-700"  },
  draft:         { label: "Draft",       className: "bg-slate-100 text-slate-700  dark:bg-slate-900/40 dark:text-slate-300  border-slate-200 dark:border-slate-700"  },
  running:       { label: "Running",     className: "bg-violet-100 text-violet-800 dark:bg-violet-900/40 dark:text-violet-300 border-violet-200 dark:border-violet-800" },
  timeout:       { label: "Timeout",     className: "bg-orange-100 text-orange-800 dark:bg-orange-900/40 dark:text-orange-300 border-orange-200 dark:border-orange-800" },
  dry_run:       { label: "Dry Run",     className: "bg-sky-100   text-sky-800    dark:bg-sky-900/40   dark:text-sky-300    border-sky-200   dark:border-sky-800"    },
};

// Exported for use by StatusValue type consumers
export type { StatusValue };

interface StatusBadgeProps {
  status: string;
  /** Optional dot indicator for "live" statuses */
  pulse?: boolean;
  size?: "sm" | "md";
  className?: string;
}

const LIVE_STATUSES = new Set(["executing", "running", "planning", "verifying"]);

export function StatusBadge({ status, pulse, size = "sm", className }: StatusBadgeProps) {
  const normalized = (status ?? "").toLowerCase().replace(/-/g, "_");
  const config = STATUS_MAP[normalized] ?? {
    label: status,
    className: "bg-muted text-muted-foreground border-border",
  };

  const showPulse = pulse ?? LIVE_STATUSES.has(normalized);

  return (
    <span
      aria-label={`Status: ${config.label}`}
      className={clsx(
        "inline-flex items-center gap-1.5 border font-medium rounded-full",
        size === "sm" ? "px-2 py-0.5 text-xs" : "px-2.5 py-1 text-sm",
        config.className,
        className,
      )}
    >
      {showPulse && (
        <span className="relative flex h-1.5 w-1.5">
          <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-current opacity-60" />
          <span className="relative inline-flex rounded-full h-1.5 w-1.5 bg-current" />
        </span>
      )}
      {config.label}
    </span>
  );
}
