import {
  Radar, RadarChart, PolarGrid, PolarAngleAxis, PolarRadiusAxis,
  ResponsiveContainer, Tooltip, Legend,
} from "recharts";
import { CHART_COLORS, CHART_AXIS_COLOR, CHART_TOOLTIP_STYLE } from "./chartTheme";

interface Props {
  data: { metric: string; value: number; fullMark?: number }[];
  /** Optional second dataset rendered as a dashed overlay for comparison */
  compareData?: { metric: string; value: number }[];
  height?: number;
  color?: string;
  compareColor?: string;
  label?: string;
  compareLabel?: string;
  className?: string;
}

export function ThemedRadarChart({
  data,
  compareData,
  height = 250,
  color,
  compareColor,
  label = "Score",
  compareLabel = "Compare",
  className,
}: Props) {
  // When compareData is provided, merge it into the main data array
  const chartData = compareData
    ? data.map((d) => {
        const cEntry = compareData.find((c) => c.metric === d.metric);
        return { ...d, compareValue: cEntry?.value ?? 0 };
      })
    : data;

  return (
    <div className={className}>
      <ResponsiveContainer width="100%" height={height}>
        <RadarChart data={chartData} margin={{ top: 10, right: 20, bottom: 10, left: 20 }}>
          <PolarGrid stroke={CHART_AXIS_COLOR} opacity={0.3} />
          <PolarAngleAxis dataKey="metric" tick={{ fill: CHART_AXIS_COLOR, fontSize: 11 }} />
          <PolarRadiusAxis
            domain={[0, 1]}
            tick={{ fill: CHART_AXIS_COLOR, fontSize: 9 }}
            tickCount={4}
          />
          <Tooltip
            contentStyle={CHART_TOOLTIP_STYLE}
            formatter={(v: number, name: string) => [
              v.toFixed(3),
              name === "value" ? label : compareLabel,
            ]}
          />
          {compareData && (
            <Legend wrapperStyle={{ fontSize: 11, color: CHART_AXIS_COLOR }} />
          )}
          <Radar
            name={label}
            dataKey="value"
            stroke={color ?? CHART_COLORS[0]}
            fill={color ?? CHART_COLORS[0]}
            fillOpacity={0.2}
            strokeWidth={2}
          />
          {compareData && (
            <Radar
              name={compareLabel}
              dataKey="compareValue"
              stroke={compareColor ?? CHART_COLORS[3]}
              fill={compareColor ?? CHART_COLORS[3]}
              fillOpacity={0.15}
              strokeWidth={2}
              strokeDasharray="4 2"
            />
          )}
        </RadarChart>
      </ResponsiveContainer>
    </div>
  );
}
