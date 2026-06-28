import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { Bell } from "lucide-react";
import { governanceApi } from "@/lib/api/client";
import { useEventStream } from "@/lib/sse/useEventStream";

export function PendingApprovalsBadge() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const { data: approvals = [] } = useQuery({
    queryKey: ["approvals"],
    queryFn: () => governanceApi.listApprovals(),
    refetchInterval: 30_000,
  });

  useEventStream(governanceApi.approvalsStreamPath(), {
    onEvent: () => qc.invalidateQueries({ queryKey: ["approvals"] }),
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
