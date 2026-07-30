"use client";

/**
 * Peer valuation comparison.
 *
 * Tier 3 throughout — these are market multiples, and the chart says so. The
 * subject company keeps its position in the peer list and is distinguished by
 * weight rather than by being moved to the front, so the reader can see where
 * it actually sits.
 *
 * Horizontal bars because the category labels are tickers and reading them
 * along a vertical axis is easier than rotating them.
 */

import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { formatMultiple } from "@/lib/format";
import type { ReportCharts } from "@/lib/types";
import {
  AXIS_TICK,
  CHART_MARGIN,
  ChartFrame,
  ChartLegend,
  ChartTooltip,
  GRID_PROPS,
  MARKET,
  NO_ANIMATION,
  RULE_COLOR,
} from "@/components/charts/chart-primitives";

type Series = NonNullable<ReportCharts["peerValuation"]>;

const SUBJECT_PE = "var(--certified)";
const PEER_PE = "color-mix(in srgb, var(--certified) 40%, var(--paper))";
const SUBJECT_EV = MARKET;
const PEER_EV = "color-mix(in srgb, var(--market) 40%, var(--paper))";

function formatValue(_dataKey: string, value: number | null): string {
  return formatMultiple(value);
}

export function PeerValuationChart({ series }: { series: Series }) {
  const height = Math.max(200, series.points.length * 44 + 40);

  return (
    <ChartFrame
      title="Peer valuation"
      unitLabel="Multiple"
      factIds={series.meta.factIds}
      height={height}
      legend={
        <ChartLegend
          items={[
            { label: "Price / earnings", color: SUBJECT_PE },
            { label: "EV / EBITDA", color: SUBJECT_EV },
          ]}
        />
      }
      note="Market data. Multiples support this comparison only; no figure in the financial highlights rests on them."
    >
      <ResponsiveContainer width="100%" height="100%">
        <BarChart
          data={[...series.points]}
          layout="vertical"
          margin={CHART_MARGIN}
          barGap={2}
        >
          <CartesianGrid {...GRID_PROPS} horizontal={false} vertical />
          <XAxis
            type="number"
            tick={AXIS_TICK}
            tickLine={false}
            axisLine={{ stroke: RULE_COLOR }}
            tickFormatter={(value: number) => `${value}x`}
          />
          <YAxis
            type="category"
            dataKey="ticker"
            tick={AXIS_TICK}
            tickLine={false}
            axisLine={false}
            width={56}
          />
          <Tooltip
            cursor={{ fill: "var(--wash)" }}
            content={(props) => (
              <ChartTooltip {...props} format={formatValue} />
            )}
          />
          <Bar
            dataKey="priceToEarnings"
            name="Price / earnings"
            maxBarSize={14}
            {...NO_ANIMATION}
          >
            {series.points.map((point) => (
              <Cell
                key={point.ticker}
                fill={point.isSubject ? SUBJECT_PE : PEER_PE}
              />
            ))}
          </Bar>
          <Bar
            dataKey="evToEbitda"
            name="EV / EBITDA"
            maxBarSize={14}
            {...NO_ANIMATION}
          >
            {series.points.map((point) => (
              <Cell
                key={point.ticker}
                fill={point.isSubject ? SUBJECT_EV : PEER_EV}
              />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
}
