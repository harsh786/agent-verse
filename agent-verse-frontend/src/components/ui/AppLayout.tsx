import { Outlet } from "react-router-dom";
import { Sidebar } from "./Sidebar";
import { TopBar } from "./TopBar";
import { CommandPalette } from "@/components/command-palette/CommandPalette";
import { useUiStore } from "@/stores/ui";
import { useTokenRefresh } from "@/hooks/useTokenRefresh";
import { clsx } from "clsx";

export function AppLayout() {
  const { sidebarOpen, toggleSidebar } = useUiStore();
  // Silently refresh the Keycloak access token before it expires (SSO mode only).
  useTokenRefresh();

  return (
    <div className="flex h-screen overflow-hidden bg-background">
      {/* Mobile backdrop — closes sidebar when tapped */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 z-20 bg-black/40 md:hidden"
          onClick={toggleSidebar}
          aria-hidden="true"
        />
      )}

      <Sidebar />

      <div
        className={clsx(
          "flex flex-col flex-1 overflow-hidden transition-all duration-200 min-w-0",
          // On desktop: offset by sidebar width
          sidebarOpen ? "md:ml-64" : "md:ml-16",
          // On mobile: no margin (sidebar overlays)
          "ml-0"
        )}
      >
        <TopBar />
        <main className="flex-1 overflow-auto p-4 md:p-6">
          <Outlet />
        </main>
      </div>
      <CommandPalette />
    </div>
  );
}
