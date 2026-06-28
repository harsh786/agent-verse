import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
} from "recharts";
import { CHART_COLORS, CHART_GRID_COLOR, CHART_AXIS_COLOR, CHART_TOOLTIP_STYLE } from "./chartTheme";

interface BarConfig { key: string; label?: string; color?: string; }
interface Props {
  data: Record<string, unknown>[];
  bars: BarConfig[];
  xKey: string;
  height?: number;
  layout?: "horizontal" | "vertical";
  formatValue?: (v: number) => string;
  className?: string;
}

export function ThemedBarChart({
  data, bars, xKey, height = 200, layout = "horizontal", formatValue, className,
}: Props) {
  return (
    <div className={className}>
      <ResponsiveContainer width="100%" height={height}>
        <BarChart data={data} layout={layout} margin={{ top: 4, right: 8, bottom: 0, left: 8 }}>
          <CartesianGrid strokeDasharray="3 3" stroke={CHART_GRID_COLOR} />
          {layout === "horizontal" ? (
            <>
              <XAxis
                dataKey={xKey}
                tick={{ fill: CHART_AXIS_COLOR, fontSize: 11 }}
                axisLine={{ stroke: CHART_GRID_COLOR }}
                tickLine={false}
              />
              <YAxis
                tick={{ fill: CHART_AXIS_COLOR, fontSize: 11 }}
                axisLine={false}
                tickLine={false}
                tickFormatter={formatValue}
              />
            </>
          ) : (
            <>
              <XAxis
                type="number"
                tick={{ fill: CHART_AXIS_COLOR, fontSize: 11 }}
                axisLine={false}
                tickLine={false}
                tickFormatter={formatValue}
              />
              <YAxis
                type="category"
                dataKey={xKey}
                tick={{ fill: CHART_AXIS_COLOR, fontSize: 11 }}
                axisLine={{ stroke: CHART_GRID_COLOR }}
                tickLine={false}
                width={80}
              />
            </>
          )}
          <Tooltip
            contentStyle={CHART_TOOLTIP_STYLE}
            formatter={formatValue ? (v: number) => [formatValue(v)] : undefined}
          />
          {bars.length > 1 && (
            <Legend wrapperStyle={{ fontSize: 11, color: CHART_AXIS_COLOR }} />
          )}
          {bars.map((b, i) => (
            <Bar
              key={b.key}
              dataKey={b.key}
              name={b.label ?? b.key}
              fill={b.color ?? CHART_COLORS[i % CHART_COLORS.length]}
              radius={[2, 2, 0, 0]}
            />
          ))}
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
