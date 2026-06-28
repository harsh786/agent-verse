/**
 * useAppHotkeys — global keyboard shortcuts for power users.
 *
 * Shortcuts (non-conflicting with browser defaults):
 *   g d  → go to Dashboard
 *   g g  → go to Goals
 *   g a  → go to Agents
 *   g t  → go to Templates
 *   g k  → go to Knowledge
 *   g r  → go to Analytics (Reports)
 *   ?    → show shortcuts overlay
 */
import { useState } from "react";
import { useHotkeys } from "react-hotkeys-hook";
import { useNavigate } from "react-router-dom";

export function useAppHotkeys() {
  const navigate = useNavigate();
  const [showHelp, setShowHelp] = useState(false);

  useHotkeys("g+d", () => navigate("/dashboard"), { preventDefault: true });
  useHotkeys("g+g", () => navigate("/goals"), { preventDefault: true });
  useHotkeys("g+a", () => navigate("/agents"), { preventDefault: true });
  useHotkeys("g+t", () => navigate("/templates"), { preventDefault: true });
  useHotkeys("g+k", () => navigate("/knowledge"), { preventDefault: true });
  useHotkeys("g+r", () => navigate("/analytics"), { preventDefault: true });
  useHotkeys("g+o", () => navigate("/observability"), { preventDefault: true });
  useHotkeys("shift+/", () => setShowHelp((v) => !v), { preventDefault: true });
  useHotkeys("escape", () => setShowHelp(false));

  return { showHelp, setShowHelp };
}
