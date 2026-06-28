import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
} from "recharts";
import { CHART_COLORS, CHART_GRID_COLOR, CHART_AXIS_COLOR, CHART_TOOLTIP_STYLE } from "./chartTheme";

interface LineConfig { key: string; label?: string; color?: string; }
interface Props {
  data: Record<string, unknown>[];
  lines: LineConfig[];
  xKey: string;
  height?: number;
  formatValue?: (v: number) => string;
  className?: string;
}

export function ThemedLineChart({ data, lines, xKey, height = 200, formatValue, className }: Props) {
  return (
    <div className={className}>
      <ResponsiveContainer width="100%" height={height}>
        <LineChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: 8 }}>
          <CartesianGrid strokeDasharray="3 3" stroke={CHART_GRID_COLOR} />
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
          <Tooltip
            contentStyle={CHART_TOOLTIP_STYLE}
            formatter={formatValue ? (v: number) => [formatValue(v)] : undefined}
          />
          {lines.length > 1 && (
            <Legend wrapperStyle={{ fontSize: 11, color: CHART_AXIS_COLOR }} />
          )}
          {lines.map((l, i) => (
            <Line
              key={l.key}
              type="monotone"
              dataKey={l.key}
              name={l.label ?? l.key}
              stroke={l.color ?? CHART_COLORS[i % CHART_COLORS.length]}
              strokeWidth={2}
              dot={false}
              activeDot={{ r: 4 }}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
