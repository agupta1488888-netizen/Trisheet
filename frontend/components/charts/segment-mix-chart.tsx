"use client";

/**
 * Segment mix over time, as a stacked bar.
 *
 * Draw order is fixed by the backend so a segment keeps its colour between
 * periods and between reports. Rows are flattened here because Recharts reads
 * one key per series — that is a reshape of values the backend supplied, not a
 * derivation of new ones.
 */

import { useMemo } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { formatSeriesValue } from "@/lib/format";
import type { ReportCharts } from "@/lib/types";
import {
  AXIS_TICK,
  CHART_MARGIN,
  ChartFrame,
  ChartLegend,
  ChartTooltip,
  GRID_PROPS,
  NO_ANIMATION,
  RULE_COLOR,
  axisTickFormatter,
  seriesColor,
} from "@/components/charts/chart-primitives";

type Series = NonNullable<ReportCharts["segmentMix"]>;

type FlatRow = { period: string } & Record<string, string | number>;

function formatValue(_dataKey: string, value: number | null): string {
  return formatSeriesValue(value);
}

export function SegmentMixChart({ series }: { series: Series }) {
  const rows = useMemo<FlatRow[]>(
    () =>
      series.points.map((point) => {
        const row: FlatRow = { period: point.period };
        for (const segment of series.segments) {
          const value = point.values[segment];
          if (value !== undefined) {
            row[segment] = value;
          }
        }
        return row;
      }),
    [series],
  );

  return (
    <ChartFrame
      title="Segment mix"
      unitLabel={series.meta.unitLabel}
      factIds={series.meta.factIds}
      legend={
        <ChartLegend
          items={series.segments.map((segment, i) => ({
            label: segment,
            color: seriesColor(i),
          }))}
        />
      }
      note="Segments as the filer reports them. A segment absent from a period was not disclosed for that period."
    >
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={rows} margin={CHART_MARGIN}>
          <CartesianGrid {...GRID_PROPS} />
          <XAxis
            dataKey="period"
            tick={AXIS_TICK}
            tickLine={false}
            axisLine={{ stroke: RULE_COLOR }}
          />
          <YAxis
            tick={AXIS_TICK}
            tickLine={false}
            axisLine={false}
            width={48}
            tickFormatter={axisTickFormatter}
          />
          <Tooltip
            cursor={{ fill: "var(--wash)" }}
            content={(props) => (
              <ChartTooltip {...props} format={formatValue} />
            )}
          />
          {series.segments.map((segment, i) => (
            <Bar
              key={segment}
              dataKey={segment}
              name={segment}
              stackId="segments"
              fill={seriesColor(i)}
              maxBarSize={56}
              {...NO_ANIMATION}
            />
          ))}
        </BarChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
}
