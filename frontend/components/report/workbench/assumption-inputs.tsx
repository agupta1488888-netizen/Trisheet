"use client";

/**
 * The reader's own assumptions.
 *
 * Collapsed behind a plain text control, because the default view assumes
 * nothing about the future and most readers want that one. Opened, it is a
 * definition list on hairline rules rather than a form card — the report view
 * is a research document and a boxed form with a submit button would read as
 * a different product.
 *
 * The inputs are bounded here and bounded again on the server, which is the
 * enforcer. An out-of-range entry is simply not carried into the URL rather
 * than being rejected with a red border.
 */

import { cn } from "@/lib/utils";
import type { ValuationAssumptions } from "@/lib/types";

interface Field {
  readonly key: keyof Omit<ValuationAssumptions, "mode">;
  readonly label: string;
  readonly suffix: string;
  readonly step: string;
  readonly min: number;
  readonly max: number;
  readonly placeholder: string;
}

const FIELDS: readonly Field[] = [
  {
    key: "discountRatePct",
    label: "Discount rate",
    suffix: "%",
    step: "0.5",
    min: 0,
    max: 60,
    placeholder: "9",
  },
  {
    key: "fcfGrowthRatePct",
    label: "Free cash flow growth",
    suffix: "%",
    step: "0.5",
    min: -50,
    max: 100,
    placeholder: "3",
  },
  {
    key: "terminalGrowthRatePct",
    label: "Terminal growth",
    suffix: "%",
    step: "0.25",
    min: -5,
    max: 10,
    placeholder: "2",
  },
  {
    key: "projectionYears",
    label: "Projection years",
    suffix: "",
    step: "1",
    min: 1,
    max: 15,
    placeholder: "5",
  },
];

export function AssumptionInputs({
  assumptions,
  onChange,
}: {
  assumptions: ValuationAssumptions;
  onChange: (next: ValuationAssumptions) => void;
}) {
  return (
    <dl className="border-t border-rule">
      {FIELDS.map((field) => {
        const value = assumptions[field.key];
        return (
          <div
            key={field.key}
            className="flex items-baseline justify-between gap-6 border-b border-rule py-2"
          >
            <dt>
              <label
                htmlFor={`assumption-${field.key}`}
                className="text-sm text-ink"
              >
                {field.label}
              </label>
            </dt>
            <dd className="flex items-baseline gap-1">
              <input
                id={`assumption-${field.key}`}
                type="number"
                inputMode="decimal"
                step={field.step}
                min={field.min}
                max={field.max}
                placeholder={field.placeholder}
                value={value === null ? "" : String(value)}
                onChange={(event) => {
                  const raw = event.target.value;
                  const parsed = raw === "" ? null : Number(raw);
                  onChange({
                    ...assumptions,
                    [field.key]:
                      parsed === null || !Number.isFinite(parsed)
                        ? null
                        : parsed,
                  });
                }}
                className={cn(
                  "figure w-24 rounded-none border-0 border-b border-rule",
                  "bg-transparent py-0.5 text-right outline-none",
                  "focus-visible:border-certified",
                  "[appearance:textfield]",
                  "[&::-webkit-inner-spin-button]:appearance-none",
                  "[&::-webkit-outer-spin-button]:appearance-none",
                )}
              />
              <span className="figure w-3 text-muted-foreground">
                {field.suffix}
              </span>
            </dd>
          </div>
        );
      })}
    </dl>
  );
}
