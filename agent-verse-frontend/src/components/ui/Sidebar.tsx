import { NavLink } from "react-router-dom";
import { clsx } from "clsx";
import {
  LayoutDashboard, Target, Bot, Plug, Calendar, BookOpen,
  Shield, Users, Activity, BarChart3, ShoppingBag, Building,
  Settings, ChevronLeft, Zap, CheckSquare, DollarSign
} from "lucide-react";
import { useUiStore } from "@/stores/ui";
import { useQuery } from "@tanstack/react-query";
import { governanceApi } from "@/lib/api/client";
import { useAuthStore } from "@/stores/auth";

interface NavItem {
  to: string;
  icon: React.ElementType;
  label: string;
  badge?: number;
}

interface NavSection {
  heading: string;
  items: NavItem[];
}

export function Sidebar() {
  const { sidebarOpen, toggleSidebar } = useUiStore();
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);

  // Poll pending approvals every 10s for badge count
  const { data: approvals = [] } = useQuery({
    queryKey: ["approvals"],
    queryFn: () => governanceApi.listApprovals(),
    refetchInterval: 10_000,
    enabled: isAuthenticated,
  });
  const pendingCount = approvals.filter((a) => a.status === "pending").length;

  const NAV_SECTIONS: NavSection[] = [
    {
      heading: "Core",
      items: [
        { to: "/dashboard", icon: LayoutDashboard, label: "Dashboard" },
        { to: "/goals",     icon: Target,          label: "Goals"     },
        { to: "/agents",    icon: Bot,             label: "Agents"    },
      ],
    },
    {
      heading: "Platform",
      items: [
        { to: "/connectors",    icon: Plug,         label: "Connectors"    },
        { to: "/knowledge",     icon: BookOpen,     label: "Knowledge"     },
        { to: "/schedules",     icon: Calendar,     label: "Schedules"     },
        { to: "/collaboration", icon: Users,        label: "Collaboration" },
      ],
    },
    {
      heading: "Governance",
      items: [
        { to: "/governance", icon: Shield,      label: "Governance"  },
        { to: "/approvals",  icon: CheckSquare, label: "Approvals",  badge: pendingCount > 0 ? pendingCount : undefined },
        { to: "/settings",   icon: Settings,    label: "Settings"    },
      ],
    },
    {
      heading: "Enterprise",
      items: [
        { to: "/marketplace",       icon: ShoppingBag, label: "Marketplace"      },
        { to: "/observability",     icon: Activity,    label: "Observability"    },
        { to: "/observability/cost", icon: DollarSign, label: "Cost Dashboard"   },
        { to: "/eval",              icon: BarChart3,   label: "Eval"             },
        { to: "/enterprise",        icon: Building,    label: "Enterprise"       },
      ],
    },
  ];

  return (
    <aside
      className={clsx(
        "fixed inset-y-0 left-0 z-30 flex flex-col bg-gray-900 text-white transition-all duration-200",
        sidebarOpen ? "w-64" : "w-16"
      )}
    >
      {/* Logo */}
      <div className="flex items-center gap-2 px-4 py-4 border-b border-gray-700">
        <Zap className="h-6 w-6 text-blue-400 flex-shrink-0" />
        {sidebarOpen && (
          <span className="font-bold text-lg truncate">AgentVerse</span>
        )}
      </div>

      {/* Navigation */}
      <nav className="flex-1 overflow-y-auto py-3" aria-label="Main navigation">
        {NAV_SECTIONS.map(({ heading, items }) => (
          <div key={heading} className="mb-1">
            {sidebarOpen && (
              <p className="px-4 pt-3 pb-1 text-xs font-semibold uppercase tracking-wider text-gray-500 select-none">
                {heading}
              </p>
            )}
            {!sidebarOpen && (
              <div className="mx-3 my-1 h-px bg-gray-700" aria-hidden="true" />
            )}
            {items.map(({ to, icon: Icon, label, badge }) => (
              <NavLink
                key={to}
                to={to}
                className={({ isActive }) =>
                  clsx(
                    "flex items-center gap-3 px-4 py-2.5 text-sm font-medium transition-colors",
                    "hover:bg-gray-800 focus-visible:bg-gray-800",
                    isActive ? "bg-gray-800 text-blue-400" : "text-gray-300"
                  )
                }
              >
                <div className="relative flex-shrink-0">
                  <Icon className="h-5 w-5" aria-hidden="true" />
                  {/* Badge on collapsed sidebar */}
                  {!sidebarOpen && badge != null && badge > 0 && (
                    <span className="absolute -top-1 -right-1 flex items-center justify-center w-4 h-4 rounded-full bg-orange-500 text-white text-xs font-bold leading-none">
                      {badge > 9 ? "9+" : badge}
                    </span>
                  )}
                </div>
                {sidebarOpen && (
                  <>
                    <span className="truncate flex-1">{label}</span>
                    {badge != null && badge > 0 && (
                      <span className="ml-auto flex items-center justify-center px-1.5 min-w-[1.25rem] h-5 rounded-full bg-orange-500 text-white text-xs font-bold">
                        {badge > 99 ? "99+" : badge}
                      </span>
                    )}
                  </>
                )}
              </NavLink>
            ))}
          </div>
        ))}
      </nav>

      {/* Collapse toggle */}
      <button
        onClick={toggleSidebar}
        className="flex items-center justify-center p-4 border-t border-gray-700 hover:bg-gray-800 transition-colors"
        aria-label={sidebarOpen ? "Collapse sidebar" : "Expand sidebar"}
      >
        <ChevronLeft
          className={clsx(
            "h-5 w-5 text-gray-400 transition-transform duration-200",
            !sidebarOpen && "rotate-180"
          )}
        />
      </button>
    </aside>
  );
}
