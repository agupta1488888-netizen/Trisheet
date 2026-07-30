"use client";

/**
 * Free cash flow and capital expenditure.
 *
 * Cash generated sits above the axis, cash spent on capital below it, and free
 * cash flow runs across as a line. Capital expenditure is disclosed as a
 * positive outflow, so the sign is flipped here purely to place the bar below
 * the axis; the tooltip carries that minus sign through, because a negative
 * number in a cash flow chart is what an outflow is. No magnitude is altered
 * and nothing is derived — free cash flow arrives already computed in m07.
 */

import { useMemo } from "react";
import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { formatSeriesValue } from "@/lib/format";
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

type Series = NonNullable<ReportCharts["cashFlow"]>;

interface PlotRow {
  period: string;
  operatingCashFlow: number | null;
  /** Negated for placement below the axis. See the note above. */
  capexOutflow: number | null;
  freeCashFlow: number | null;
}

function formatValue(_dataKey: string, value: number | null): string {
  return formatSeriesValue(value);
}

export function CashFlowChart({ series }: { series: Series }) {
  const rows = useMemo<PlotRow[]>(
    () =>
      series.points.map((point) => ({
        period: point.period,
        operatingCashFlow: point.operatingCashFlow,
        capexOutflow: point.capex === null ? null : -point.capex,
        freeCashFlow: point.freeCashFlow,
      })),
    [series],
  );

  return (
    <ChartFrame
      title="Cash flow and capital expenditure"
      unitLabel={series.meta.unitLabel}
      factIds={series.meta.factIds}
      legend={
        <ChartLegend
          items={[
            { label: "Cash from operations", color: CERTIFIED },
            { label: "Capital expenditure (outflow)", color: INK_MUTED },
            { label: "Free cash flow", color: MARKET },
          ]}
        />
      }
      note="Free cash flow is calculated as cash from operations less capital expenditure."
    >
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={rows} margin={CHART_MARGIN} stackOffset="sign">
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
            width={52}
            tickFormatter={axisTickFormatter}
          />
          <ReferenceLine y={0} stroke={RULE_COLOR} />
          <Tooltip
            cursor={{ fill: "var(--wash)" }}
            content={(props) => (
              <ChartTooltip {...props} format={formatValue} />
            )}
          />
          <Bar
            dataKey="operatingCashFlow"
            name="Cash from operations"
            fill={CERTIFIED}
            maxBarSize={44}
            {...NO_ANIMATION}
          />
          <Bar
            dataKey="capexOutflow"
            name="Capital expenditure"
            fill={INK_MUTED}
            maxBarSize={44}
            {...NO_ANIMATION}
          />
          <Line
            type="linear"
            dataKey="freeCashFlow"
            name="Free cash flow"
            stroke={MARKET}
            strokeWidth={1.5}
            dot={{ r: 2.5, fill: MARKET, strokeWidth: 0 }}
            {...NO_ANIMATION}
          />
        </ComposedChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
}
