import {
  Radar, RadarChart, PolarGrid, PolarAngleAxis, PolarRadiusAxis,
  ResponsiveContainer, Tooltip,
} from "recharts";
import { CHART_COLORS, CHART_AXIS_COLOR, CHART_TOOLTIP_STYLE } from "./chartTheme";

interface Props {
  data: { metric: string; value: number; fullMark?: number }[];
  height?: number;
  color?: string;
  label?: string;
  className?: string;
}

export function ThemedRadarChart({ data, height = 250, color, label = "Score", className }: Props) {
  return (
    <div className={className}>
      <ResponsiveContainer width="100%" height={height}>
        <RadarChart data={data} margin={{ top: 10, right: 20, bottom: 10, left: 20 }}>
          <PolarGrid stroke={CHART_AXIS_COLOR} opacity={0.3} />
          <PolarAngleAxis dataKey="metric" tick={{ fill: CHART_AXIS_COLOR, fontSize: 11 }} />
          <PolarRadiusAxis
            domain={[0, 1]}
            tick={{ fill: CHART_AXIS_COLOR, fontSize: 9 }}
            tickCount={4}
          />
          <Tooltip
            contentStyle={CHART_TOOLTIP_STYLE}
            formatter={(v: number) => [v.toFixed(3), label]}
          />
          <Radar
            name={label}
            dataKey="value"
            stroke={color ?? CHART_COLORS[0]}
            fill={color ?? CHART_COLORS[0]}
            fillOpacity={0.2}
            strokeWidth={2}
          />
        </RadarChart>
      </ResponsiveContainer>
    </div>
  );
}
