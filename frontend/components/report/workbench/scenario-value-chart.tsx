"use client";

/**
 * Value per share, drawn against the same three named cases
 * `ScenarioComparison` tabulates.
 *
 * Value per share only. Equity value lives on a different scale, and a bar
 * chart cannot share an axis between the two without one of them flattening
 * to nothing beside the other — the table beside this one is where a reader
 * goes for equity value.
 *
 * Every bar is a `ValuationFigure`, and a `ValuationFigure` is deliberately
 * not a `Fact` (see `lib/types.ts`): it carries no accession number, because
 * a projection cites no filing. `ChartFrame` normally hangs a source marker
 * off `factIds`; this chart passes none and states the same absence in its
 * note instead, the way the workbench's own tables mark a projection with a
 * dagger rather than a citation.
 *
 * The tooltip reads each bar's `display` string rather than reformatting its
 * `value` through `Intl` — the browser does not scale, divide or format a
 * `ValuationFigure`, the same rule the tables next to this chart follow.
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

import { WORKBENCH_ASSUMPTION_NOTE } from "@/lib/constants";
import { formatSeriesValue } from "@/lib/format";
import type { ScenarioCase } from "@/lib/types";
import {
  AXIS_TICK,
  BarValueLabel,
  CERTIFIED,
  CHART_MARGIN,
  ChartFrame,
  ChartTooltip,
  GRID_PROPS,
  INK_MUTED,
  NO_ANIMATION,
  RULE_COLOR,
} from "@/components/charts/chart-primitives";

interface ScenarioPoint {
  name: string;
  label: string;
  value: number;
}

function buildPoints(scenarios: readonly ScenarioCase[]): {
  points: ScenarioPoint[];
  displayByValue: ReadonlyMap<number, string>;
} {
  const points: ScenarioPoint[] = [];
  const displayByValue = new Map<number, string>();

  for (const scenario of scenarios) {
    const figure = scenario.estimate.valuePerShare;
    if (figure === null) {
      continue;
    }
    points.push({
      name: scenario.name,
      label: scenario.name.charAt(0).toUpperCase() + scenario.name.slice(1),
      value: figure.value,
    });
    displayByValue.set(figure.value, figure.display);
  }

  return { points, displayByValue };
}

export function ScenarioValueChart({
  scenarios,
}: {
  scenarios: readonly ScenarioCase[];
}) {
  const { points, displayByValue } = buildPoints(scenarios);
  if (points.length === 0) {
    return null;
  }

  function formatDisplay(value: number): string {
    return displayByValue.get(value) ?? formatSeriesValue(value);
  }

  function formatValue(_dataKey: string, value: number | null): string {
    return value === null ? formatSeriesValue(value) : formatDisplay(value);
  }

  return (
    <ChartFrame
      title="Value per share, by named growth case"
      unitLabel="Per share"
      factIds={[]}
      height={points.length * 48 + 32}
      note={WORKBENCH_ASSUMPTION_NOTE}
    >
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={points} layout="vertical" margin={CHART_MARGIN}>
          <CartesianGrid {...GRID_PROPS} horizontal={false} vertical />
          <XAxis
            type="number"
            tick={AXIS_TICK}
            tickLine={false}
            axisLine={{ stroke: RULE_COLOR }}
          />
          <YAxis
            type="category"
            dataKey="label"
            tick={AXIS_TICK}
            tickLine={false}
            axisLine={false}
            width={44}
          />
          <Tooltip
            cursor={{ fill: "var(--wash)" }}
            content={(props) => (
              <ChartTooltip {...props} format={formatValue} />
            )}
          />
          <Bar
            dataKey="value"
            name="Value per share"
            maxBarSize={22}
            label={<BarValueLabel formatter={formatDisplay} />}
            {...NO_ANIMATION}
          >
            {points.map((point) => (
              <Cell
                key={point.name}
                fill={point.name === "base" ? CERTIFIED : INK_MUTED}
              />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
}
