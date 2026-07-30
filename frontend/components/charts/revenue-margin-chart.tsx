"use client";

/**
 * Revenue and margin trend.
 *
 * Revenue as bars on the left axis, margins as lines on the right. The two
 * scales are unrelated, which is the point of the chart: the reader is looking
 * for margin moving independently of revenue.
 *
 * Every value plotted arrives already computed, in its display scale.
 */

import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { formatPercent, formatSeriesValue } from "@/lib/format";
import type { ReportCharts } from "@/lib/types";
import {
  AXIS_TICK,
  CERTIFIED,
  CHART_MARGIN,
  ChartFrame,
  ChartLegend,
  ChartTooltip,
  GRID_PROPS,
  INK_MUTED,
  MARKET,
  NO_ANIMATION,
  RULE_COLOR,
  axisTickFormatter,
} from "@/components/charts/chart-primitives";

type Series = NonNullable<ReportCharts["revenueMargin"]>;

const KEY_LABELS: Readonly<Record<string, string>> = {
  revenue: "Revenue",
  grossMarginPct: "Gross margin",
  operatingMarginPct: "Operating margin",
};

function formatValue(dataKey: string, value: number | null): string {
  return dataKey === "revenue"
    ? formatSeriesValue(value)
    : formatPercent(value);
}

export function RevenueMarginChart({ series }: { series: Series }) {
  return (
    <ChartFrame
      title="Revenue and margin"
      unitLabel={`${series.meta.unitLabel} · per cent`}
      factIds={series.meta.factIds}
      legend={
        <ChartLegend
          items={[
            { label: "Revenue", color: CERTIFIED },
            { label: "Gross margin", color: MARKET },
            { label: "Operating margin", color: INK_MUTED },
          ]}
        />
      }
    >
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={[...series.points]} margin={CHART_MARGIN}>
          <CartesianGrid {...GRID_PROPS} />
          <XAxis
            dataKey="period"
            tick={AXIS_TICK}
            tickLine={false}
            axisLine={{ stroke: RULE_COLOR }}
          />
          <YAxis
            yAxisId="value"
            tick={AXIS_TICK}
            tickLine={false}
            axisLine={false}
            width={48}
            tickFormatter={axisTickFormatter}
          />
          <YAxis
            yAxisId="percent"
            orientation="right"
            tick={AXIS_TICK}
            tickLine={false}
            axisLine={false}
            width={40}
            tickFormatter={(value: number) => `${value}%`}
          />
          <Tooltip
            cursor={{ fill: "var(--wash)" }}
            content={(props) => (
              <ChartTooltip {...props} format={formatValue} />
            )}
          />
          <Bar
            yAxisId="value"
            dataKey="revenue"
            name={KEY_LABELS.revenue}
            fill={CERTIFIED}
            maxBarSize={44}
            {...NO_ANIMATION}
          />
          <Line
            yAxisId="percent"
            type="linear"
            dataKey="grossMarginPct"
            name={KEY_LABELS.grossMarginPct}
            stroke={MARKET}
            strokeWidth={1.5}
            dot={{ r: 2.5, fill: MARKET, strokeWidth: 0 }}
            {...NO_ANIMATION}
          />
          <Line
            yAxisId="percent"
            type="linear"
            dataKey="operatingMarginPct"
            name={KEY_LABELS.operatingMarginPct}
            stroke={INK_MUTED}
            strokeWidth={1.5}
            strokeDasharray="4 3"
            dot={{ r: 2.5, fill: INK_MUTED, strokeWidth: 0 }}
            {...NO_ANIMATION}
          />
        </ComposedChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
}
