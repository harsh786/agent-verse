/**
 * CSS-variable-aware color palette for Recharts.
 * These use oklch-based Tailwind 4 tokens that work in light + dark.
 */
export const CHART_COLORS = [
  "hsl(217, 91%, 60%)",   // blue-500
  "hsl(142, 71%, 45%)",   // green-500
  "hsl(0, 84%, 60%)",     // red-500
  "hsl(271, 91%, 65%)",   // violet-500
  "hsl(30, 86%, 57%)",    // orange-500
  "hsl(197, 71%, 53%)",   // cyan-500
  "hsl(330, 81%, 60%)",   // pink-500
  "hsl(48, 100%, 54%)",   // yellow-500
];

export const CHART_GRID_COLOR = "hsl(var(--border))";
export const CHART_AXIS_COLOR = "hsl(var(--muted-foreground))";
export const CHART_BG_COLOR = "hsl(var(--card))";
export const CHART_TOOLTIP_STYLE = {
  backgroundColor: "hsl(var(--popover))",
  border: "1px solid hsl(var(--border))",
  borderRadius: "8px",
  color: "hsl(var(--popover-foreground))",
  fontSize: "12px",
};
