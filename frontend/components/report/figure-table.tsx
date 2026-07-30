"use client";

/**
 * A table of cited figures.
 *
 * Hairline rules, no cards, no shadows. Figures are right-aligned monospace
 * with tabular numerals so the columns read as a column. Every cell is a
 * `Figure`, so no number reaches the page without its marker.
 *
 * The table scrolls horizontally inside its own container on a narrow screen
 * rather than forcing the page to.
 */

import { cn } from "@/lib/utils";
import { NOT_DISCLOSED } from "@/lib/constants";
import type { FigureTable as FigureTableModel } from "@/lib/types";
import { CalculatedLabel, Figure } from "@/components/report/figure";

/** Row label column, in rem. Wide enough for "Free cash flow conversion". */
const LABEL_COLUMN_REM = 12;
/** Each period column. Wide enough for a nine-figure number and its marker. */
const PERIOD_COLUMN_REM = 7;

export function FigureTable({ table }: { table: FigureTableModel }) {
  // Scaled to the number of periods rather than fixed: a one-column snapshot
  // table fits a narrow screen without scrolling, and a five-year table
  // scrolls inside its own container instead of squeezing the figures.
  const minWidth = `${LABEL_COLUMN_REM + table.periods.length * PERIOD_COLUMN_REM}rem`;

  return (
    <figure className="mt-6">
      <div className="overflow-x-auto">
        <table
          className="w-full border-collapse text-sm"
          style={{ minWidth }}
        >
          <caption className="sr-only">{table.caption}</caption>
          <thead>
            <tr className="border-b border-rule">
              <th
                scope="col"
                className="py-2 pr-4 text-left text-xs font-medium text-muted-foreground"
              >
                {table.caption}
              </th>
              {table.periods.map((period) => (
                <th
                  key={period}
                  scope="col"
                  className="ref w-28 py-2 pl-4 text-right text-xs font-medium text-muted-foreground"
                >
                  {period}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {table.rows.map((row) => (
              <tr
                key={row.label}
                className={cn(
                  "border-b border-rule/70",
                  row.emphasis === "total" && "font-medium",
                )}
              >
                <th
                  scope="row"
                  className="py-2 pr-4 text-left font-normal text-ink"
                >
                  <span className={cn(row.emphasis === "total" && "font-medium")}>
                    {row.label}
                  </span>
                  {row.emphasis === "derived" && row.factIds[0] != null ? (
                    <CalculatedLabel factId={row.factIds[0]} />
                  ) : null}
                </th>
                {row.factIds.map((factId, columnIndex) => (
                  <td
                    key={`${row.label}-${table.periods[columnIndex] ?? columnIndex}`}
                    className="py-2 pl-4 text-right align-baseline"
                  >
                    {factId === null ? (
                      <span className="text-muted-foreground">
                        {NOT_DISCLOSED}
                      </span>
                    ) : (
                      <Figure factId={factId} />
                    )}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <figcaption className="mt-2 text-xs text-muted-foreground">
        {table.unitNote}
      </figcaption>
    </figure>
  );
}
