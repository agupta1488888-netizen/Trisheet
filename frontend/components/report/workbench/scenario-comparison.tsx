"use client";

/**
 * The same calculation under named growth cases.
 *
 * The names are the filer-independent bear/base/bull the backend defines, and
 * the deltas behind them are fixed illustrative steps — never fitted to this
 * company, which would make them look derived from something.
 */

import { AssumptionFigure, AssumptionRate } from "@/components/report/workbench/assumption-figure";
import type { ScenarioCase } from "@/lib/types";

export function ScenarioComparison({
  scenarios,
  note,
}: {
  scenarios: readonly ScenarioCase[];
  note: string;
}) {
  if (scenarios.length === 0) {
    return null;
  }

  return (
    <figure className="space-y-3">
      <figcaption className="font-display text-base text-ink">
        Under named growth cases
      </figcaption>
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr className="border-b border-rule">
              <th
                scope="col"
                className="py-2 pr-4 text-left text-xs font-normal text-muted-foreground"
              >
                Case
              </th>
              <th
                scope="col"
                className="py-2 pl-4 text-right text-xs font-normal text-muted-foreground"
              >
                Growth
              </th>
              <th
                scope="col"
                className="py-2 pl-4 text-right text-xs font-normal text-muted-foreground"
              >
                Equity value
              </th>
              <th
                scope="col"
                className="py-2 pl-4 text-right text-xs font-normal text-muted-foreground"
              >
                Per share
              </th>
            </tr>
          </thead>
          <tbody>
            {scenarios.map((scenario) => (
              <tr key={scenario.name} className="border-b border-rule/70">
                <th
                  scope="row"
                  className="py-2 pr-4 text-left text-sm font-normal text-ink"
                >
                  {scenario.name.charAt(0).toUpperCase() + scenario.name.slice(1)}
                </th>
                <td className="py-2 pl-4 text-right">
                  <AssumptionRate assumption={scenario.fcfGrowthRate} />
                </td>
                <td className="py-2 pl-4 text-right">
                  <AssumptionFigure
                    figure={scenario.estimate.equityValue}
                    note={note}
                  />
                </td>
                <td className="py-2 pl-4 text-right">
                  <AssumptionFigure
                    figure={scenario.estimate.valuePerShare}
                    note={note}
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </figure>
  );
}
