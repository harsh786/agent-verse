import { NavLink } from "react-router-dom";
import { clsx } from "clsx";
import {
  LayoutDashboard, Target, Bot, Plug, Calendar, BookOpen,
  Shield, Users, Activity, BarChart3, ShoppingBag, Building,
  Settings, ChevronLeft, Zap
} from "lucide-react";
import { useUiStore } from "@/stores/ui";

const NAV_ITEMS = [
  { to: "/dashboard", icon: LayoutDashboard, label: "Dashboard" },
  { to: "/goals", icon: Target, label: "Goals" },
  { to: "/agents", icon: Bot, label: "Agents" },
  { to: "/connectors", icon: Plug, label: "Connectors" },
  { to: "/schedules", icon: Calendar, label: "Schedules" },
  { to: "/knowledge", icon: BookOpen, label: "Knowledge" },
  { to: "/governance", icon: Shield, label: "Governance" },
  { to: "/collaboration", icon: Users, label: "Collaboration" },
  { to: "/observability", icon: Activity, label: "Observability" },
  { to: "/eval", icon: BarChart3, label: "Eval" },
  { to: "/marketplace", icon: ShoppingBag, label: "Marketplace" },
  { to: "/enterprise", icon: Building, label: "Enterprise" },
  { to: "/settings", icon: Settings, label: "Settings" },
];

export function Sidebar() {
  const { sidebarOpen, toggleSidebar } = useUiStore();

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
      <nav className="flex-1 overflow-y-auto py-4" aria-label="Main navigation">
        {NAV_ITEMS.map(({ to, icon: Icon, label }) => (
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
            <Icon className="h-5 w-5 flex-shrink-0" aria-hidden="true" />
            {sidebarOpen && <span className="truncate">{label}</span>}
          </NavLink>
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
