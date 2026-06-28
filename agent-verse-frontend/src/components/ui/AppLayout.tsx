import { Outlet } from "react-router-dom";
import { Sidebar } from "./Sidebar";
import { TopBar } from "./TopBar";
import { CommandPalette } from "@/components/command-palette/CommandPalette";
import { useUiStore } from "@/stores/ui";
import { useTokenRefresh } from "@/hooks/useTokenRefresh";
import { useAppHotkeys } from "@/hooks/useAppHotkeys";
import { clsx } from "clsx";

export function AppLayout() {
  const { sidebarOpen, toggleSidebar } = useUiStore();
  // Silently refresh the Keycloak access token before it expires (SSO mode only).
  useTokenRefresh();

  const { showHelp, setShowHelp } = useAppHotkeys();

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

      {showHelp && (
        <div
          className="fixed inset-0 z-[150] flex items-center justify-center p-4"
          onClick={() => setShowHelp(false)}
        >
          <div className="absolute inset-0 bg-black/50" aria-hidden="true" />
          <div
            className="relative bg-card border border-border rounded-xl shadow-xl p-6 max-w-sm w-full"
            onClick={(e) => e.stopPropagation()}
          >
            <h2 className="text-sm font-semibold mb-4">Keyboard Shortcuts</h2>
            <div className="space-y-2 text-sm">
              {[
                ["g d", "Go to Dashboard"],
                ["g g", "Go to Goals"],
                ["g a", "Go to Agents"],
                ["g t", "Go to Templates"],
                ["g k", "Go to Knowledge"],
                ["g r", "Go to Analytics"],
                ["⌘K", "Command Palette"],
                ["?", "Toggle this help"],
                ["Esc", "Close overlays"],
              ].map(([key, label]) => (
                <div key={key} className="flex items-center justify-between">
                  <span className="text-muted-foreground">{label}</span>
                  <kbd className="px-2 py-0.5 text-xs font-mono bg-muted border border-border rounded">
                    {key}
                  </kbd>
                </div>
              ))}
            </div>
            <button
              onClick={() => setShowHelp(false)}
              className="mt-4 w-full py-2 text-sm border border-input rounded-lg hover:bg-muted/50 transition-colors"
            >
              Close
            </button>
          </div>
        </div>
      )}

      <CommandPalette />
    </div>
  );
}
