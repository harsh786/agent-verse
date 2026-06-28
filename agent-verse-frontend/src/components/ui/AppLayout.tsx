import { Outlet } from "react-router-dom";
import { Sidebar } from "./Sidebar";
import { TopBar } from "./TopBar";
import { CommandPalette } from "@/components/command-palette/CommandPalette";
import { useUiStore } from "@/stores/ui";
import { useTokenRefresh } from "@/hooks/useTokenRefresh";
import { clsx } from "clsx";

export function AppLayout() {
  const sidebarOpen = useUiStore((s) => s.sidebarOpen);
  // Silently refresh the Keycloak access token before it expires (SSO mode only).
  useTokenRefresh();

  return (
    <div className="flex h-screen overflow-hidden bg-background">
      <Sidebar />
      <div
        className={clsx(
          "flex flex-col flex-1 overflow-hidden transition-all duration-200",
          sidebarOpen ? "ml-64" : "ml-16"
        )}
      >
        <TopBar />
        <main className="flex-1 overflow-auto p-6">
          <Outlet />
        </main>
      </div>
      <CommandPalette />
    </div>
  );
}
