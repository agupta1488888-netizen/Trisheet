"use client";

/**
 * Value re-run across discount rate and growth at once.
 *
 * Two axes rather than two separate tables, because the assumptions interact:
 * a rate that looks tolerable at one growth assumption may not be at another,
 * and a pair of one-dimensional tables cannot show that.
 *
 * Deliberately not a heat map. Colouring the cells would rank them, and this
 * report does not rank a projection — it states what each assumption produces
 * and leaves the judgement to the reader. The only mark is a hairline on the
 * cell nearest the traded price, which is a fact about the market rather than
 * an opinion about the cell.
 */

import { cn } from "@/lib/utils";
import { NOT_DISCLOSED } from "@/lib/constants";
import { AssumptionFigure } from "@/components/report/workbench/assumption-figure";
import type { SensitivityGrid as SensitivityGridData } from "@/lib/types";

export function SensitivityGrid({
  grid,
  note,
}: {
  grid: SensitivityGridData;
  note: string;
}) {
  if (grid.unavailableReason !== null || grid.cells.length === 0) {
    return (
      <p className="border-l-2 border-l-market pl-4 text-sm leading-relaxed text-muted-foreground">
        {grid.unavailableReason ??
          "There is not enough disclosed for this filer to build a sensitivity grid."}
      </p>
    );
  }

  const columns = grid.fcfGrowthRates.length;

  return (
    <figure className="space-y-3">
      <figcaption className="font-display text-base text-ink">
        Value per share across both assumptions
      </figcaption>
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-sm">
          <caption className="sr-only">
            Value per share by discount rate and free cash flow growth rate
          </caption>
          <thead>
            <tr className="border-b border-rule">
              <th
                scope="col"
                className="py-2 pr-4 text-left text-xs font-normal text-muted-foreground"
              >
                Discount rate
              </th>
              {grid.fcfGrowthRates.map((rate) => (
                <th
                  key={rate.display}
                  scope="col"
                  className="ref py-2 pl-4 text-right text-xs font-normal text-muted-foreground"
                >
                  {rate.display}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {grid.discountRates.map((discount, row) => (
              <tr key={discount.display} className="border-b border-rule/70">
                <th
                  scope="row"
                  className="ref py-2 pr-4 text-left text-xs font-normal text-muted-foreground"
                >
                  {discount.display}
                </th>
                {grid.cells
                  .slice(row * columns, row * columns + columns)
                  .map((cell, column) => (
                    <td
                      key={`${discount.display}-${column}`}
                      className={cn("py-2 pl-4 text-right")}
                    >
                      {cell.valuePerShare === null ? (
                        <span className="figure text-muted-foreground">
                          {NOT_DISCLOSED}
                        </span>
                      ) : (
                        <AssumptionFigure
                          figure={cell.valuePerShare}
                          note={`${cell.discountRate.display} discount rate, ${cell.fcfGrowthRate.display} growth. ${note}`}
                        />
                      )}
                    </td>
                  ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-xs leading-relaxed text-muted-foreground">
        Rows are the discount rate, columns the free cash flow growth rate.
        Every cell is a projection.
      </p>
    </figure>
  );
}
