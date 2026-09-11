import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { Bell } from "lucide-react";
import { governanceApi } from "@/lib/api/client";

export function PendingApprovalsBadge() {
  const navigate = useNavigate();
  // This badge lives in the TopBar, so it renders on EVERY page. It used to also
  // hold an always-on SSE stream to /governance/approvals/stream — an app-wide
  // persistent connection that (with the goal/org/voice streams) saturated the
  // browser's ~6-connections-per-host limit and starved regular fetches, leaving
  // pages stuck on loading spinners. A header badge doesn't need sub-second
  // latency, so a light poll is enough; the dedicated Approvals page keeps its
  // own live stream.
  const { data: approvals = [] } = useQuery({
    queryKey: ["approvals"],
    queryFn: () => governanceApi.listApprovals(),
    refetchInterval: 20_000,
  });

  const pending = approvals.filter((a) => a.status === "pending").length;
  if (pending === 0) return null;

  return (
    <button
      onClick={() => navigate("/approvals")}
      aria-label="Pending approvals"
      className="relative p-1.5 rounded-md hover:bg-accent transition-colors text-muted-foreground"
    >
      <Bell className="h-4 w-4" aria-hidden="true" />
      <span className="absolute -top-0.5 -right-0.5 inline-flex items-center justify-center min-w-[1.1rem] h-[1.1rem] px-1 rounded-full text-[10px] font-bold bg-orange-500 text-white">
        {pending}
      </span>
    </button>
  );
}
