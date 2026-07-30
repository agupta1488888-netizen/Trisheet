"use client";

/**
 * Shared chart furniture.
 *
 * Charts in this product are diagrams in a research document, not dashboard
 * widgets. Consequences, all deliberate:
 *
 *   - No gradients, no drop shadows, no rounded bar caps, nothing 3-D.
 *   - Hairline axes and a horizontal grid only. Vertical grid lines add ink
 *     without adding information when the x-axis is a handful of periods.
 *   - Animation is off. A figure that slides into place is a figure you cannot
 *     read yet, and it makes the page behave differently for a reader who has
 *     asked for reduced motion.
 *   - Colours come from CSS variables, so a chart follows the palette rather
 *     than restating it, and inverts correctly in a forced dark context.
 */

import type { ReactNode } from "react";
import type { TooltipContentProps } from "recharts";

import { cn } from "@/lib/utils";
import { formatAxisValue } from "@/lib/format";
import { markersFor } from "@/lib/provenance";
import { SourceMarker } from "@/components/report/figure";
import { useProvenance } from "@/components/report/provenance-context";

/**
 * The series ramp. Six steps is the ceiling: past that a stacked bar stops
 * being readable and the answer is fewer series, not more colours.
 */
export const SERIES_COLORS: readonly string[] = [
  "var(--series-1)",
  "var(--series-2)",
  "var(--series-3)",
  "var(--series-4)",
  "var(--series-5)",
  "var(--series-6)",
];

export function seriesColor(index: number): string {
  return SERIES_COLORS[index % SERIES_COLORS.length] ?? "var(--series-1)";
}

export const RULE_COLOR = "var(--rule)";
export const AXIS_TEXT_COLOR = "var(--ink-muted)";
export const CERTIFIED = "var(--certified)";
export const MARKET = "var(--market)";
export const FLAG = "var(--flag)";
export const INK_MUTED = "var(--ink-muted)";

/** Axis label styling, applied identically to every chart. */
export const AXIS_TICK = {
  fill: AXIS_TEXT_COLOR,
  fontSize: 11,
  fontFamily: "var(--font-plex-mono)",
} as const;

export const GRID_PROPS = {
  stroke: RULE_COLOR,
  strokeDasharray: "0",
  vertical: false,
} as const;

/** Charts never animate. See the note at the top of this file. */
export const NO_ANIMATION = { isAnimationActive: false } as const;

export const CHART_MARGIN = { top: 8, right: 8, bottom: 0, left: 0 } as const;

/**
 * Tooltip body. Values are formatted by the caller, because only the caller
 * knows whether a series is a currency, a percentage or a multiple.
 *
 * The props keep Recharts' own default generics rather than narrowing to
 * `<number, string>`: `Tooltip`'s `content` prop is typed against the defaults,
 * and narrowing here makes the component unassignable to it. A payload entry
 * that is not a number is passed to `format` as null, which is what renders
 * "Not disclosed".
 */
export function ChartTooltip({
  active,
  payload,
  label,
  format,
}: TooltipContentProps & {
  format: (dataKey: string, value: number | null) => string;
}) {
  if (active !== true || payload === undefined || payload.length === 0) {
    return null;
  }

  return (
    <div className="border border-rule bg-paper px-3 py-2 text-xs">
      <p className="ref mb-1.5 text-ink">{String(label)}</p>
      <ul className="space-y-1">
        {payload.map((entry) => {
          const key = String(entry.dataKey ?? entry.name ?? "");
          const value = typeof entry.value === "number" ? entry.value : null;
          return (
            <li key={key} className="flex items-baseline gap-3">
              <span
                aria-hidden="true"
                className="inline-block size-1.5 shrink-0 translate-y-px"
                style={{ backgroundColor: entry.color }}
              />
              <span className="mr-auto text-muted-foreground">
                {String(entry.name ?? key)}
              </span>
              <span className="figure text-ink">{format(key, value)}</span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/** Compact axis ticks, so the y-axis stays narrow. */
export function axisTickFormatter(value: number): string {
  return formatAxisValue(value);
}

/**
 * The frame every chart sits in: a title, the unit it is measured in, the
 * markers naming the facts it plots, and a fixed height so the page does not
 * reflow while the chart mounts.
 */
export function ChartFrame({
  title,
  unitLabel,
  factIds,
  note,
  height = 260,
  legend,
  children,
  className,
}: {
  title: string;
  unitLabel: string;
  factIds: readonly string[];
  note?: string;
  height?: number;
  /** Rendered below the plot area, outside its fixed height. */
  legend?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  const { index } = useProvenance();
  const markers = markersFor(index, factIds);

  return (
    <figure className={cn("mt-8", className)}>
      <figcaption className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 border-b border-rule pb-2">
        <h3 className="text-sm font-medium text-ink">
          {title}
          <span className="whitespace-nowrap">
            {markers.map((marker) => {
              const card = index.cards[marker - 1];
              return card === undefined ? null : (
                <SourceMarker key={card.id} card={card} />
              );
            })}
          </span>
        </h3>
        <span className="ref text-[0.7rem] text-muted-foreground">
          {unitLabel}
        </span>
      </figcaption>

      <div style={{ height }} className="mt-3 w-full">
        {children}
      </div>

      {legend}

      {note === undefined ? null : (
        <p className="mt-2 text-xs text-muted-foreground">{note}</p>
      )}
    </figure>
  );
}

/**
 * A legend, drawn by us rather than by Recharts so it matches the compliance
 * strip and the rail.
 */
export function ChartLegend({
  items,
}: {
  items: readonly { label: string; color: string }[];
}) {
  return (
    <ul className="mt-3 flex flex-wrap gap-x-5 gap-y-1">
      {items.map((item) => (
        <li key={item.label} className="flex items-baseline gap-1.5 text-[0.7rem]">
          <span
            aria-hidden="true"
            className="inline-block size-1.5 shrink-0 translate-y-px"
            style={{ backgroundColor: item.color }}
          />
          <span className="text-muted-foreground">{item.label}</span>
        </li>
      ))}
    </ul>
  );
}
