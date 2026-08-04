"use client";

/**
 * Analysis depth.
 *
 * A radio group rather than a dropdown: there are three options, they are
 * mutually exclusive, and the difference between them is worth reading before
 * choosing. Native radio inputs carry the keyboard behaviour and the screen
 * reader semantics for free — the visible control is the label.
 */

import { useId } from "react";

import { cn } from "@/lib/utils";
import { CUSTOM_PERIODS_MAX, CUSTOM_PERIODS_MIN, DEPTH_OPTIONS } from "@/lib/constants";
import type { AnalysisDepth } from "@/lib/types";

/** `null` reads as "not entered yet", distinct from an in-range value. */
function customPeriodsError(customPeriods: number | null): string | null {
  if (customPeriods === null) {
    return "Enter a number of years.";
  }
  if (
    !Number.isInteger(customPeriods) ||
    customPeriods < CUSTOM_PERIODS_MIN ||
    customPeriods > CUSTOM_PERIODS_MAX
  ) {
    return `Enter a whole number between ${CUSTOM_PERIODS_MIN} and ${CUSTOM_PERIODS_MAX}.`;
  }
  return null;
}

export function DepthSelector({
  value,
  onChange,
  customPeriods,
  onCustomPeriodsChange,
  name,
  disabled = false,
}: {
  value: AnalysisDepth;
  onChange: (value: AnalysisDepth) => void;
  /** Only meaningful when `value` is "custom"; ignored otherwise. */
  customPeriods: number | null;
  onCustomPeriodsChange: (value: number | null) => void;
  name: string;
  disabled?: boolean;
}) {
  const customInputId = useId();
  const isCustomSelected = value === "custom";
  const customError = isCustomSelected
    ? customPeriodsError(customPeriods)
    : null;

  return (
    <fieldset disabled={disabled} className="@container min-w-0">
      <legend className="text-sm font-semibold tracking-wide text-muted-foreground uppercase">
        Analysis depth
      </legend>

      {/* Container-query breakpoints, not viewport ones: this card sits
          inside a two-column hero, so at desktop viewport widths it is
          still only ~500px wide. `lg:` would key off the viewport (1024px+)
          and force 4 columns into a card nowhere near that wide; `@sm`/`@2xl`
          key off this fieldset's own rendered width instead. */}
      <div className="mt-3 grid grid-cols-1 gap-2.5 @sm:grid-cols-2 @2xl:grid-cols-4">
        {DEPTH_OPTIONS.map((option) => {
          const isSelected = option.value === value;
          const isCustom = option.value === "custom";
          return (
            <label
              key={option.value}
              className={cn(
                "group flex cursor-pointer flex-col gap-1 rounded-lg border-2 px-3 py-2.5 transition-colors motion-reduce:transition-none",
                isSelected
                  ? "border-emerald-500 bg-emerald-50"
                  : "border-slate-200 bg-white hover:border-slate-300",
                "has-[input:focus-visible]:outline has-[input:focus-visible]:outline-2 has-[input:focus-visible]:outline-offset-2 has-[input:focus-visible]:outline-emerald-500",
                disabled && "cursor-not-allowed opacity-50",
              )}
            >
              <span className="flex items-baseline gap-2">
                <input
                  type="radio"
                  name={name}
                  value={option.value}
                  checked={isSelected}
                  onChange={() => {
                    onChange(option.value);
                  }}
                  className="sr-only"
                />
                <span
                  className={cn(
                    "text-base",
                    isSelected
                      ? "font-semibold text-emerald-800"
                      : "font-medium text-ink",
                  )}
                >
                  {option.label}
                </span>
                {/* The years field sits on the title row, in the slot the
                    other options use for their "3y" / "5y" label, rather
                    than on a line of its own below the summary. On its own
                    line it made this whole grid row taller and left dead
                    space in the option beside it. */}
                {isCustom ? (
                  <span className="flex items-center gap-1.5">
                    <label
                      htmlFor={customInputId}
                      className="text-xs text-muted-foreground"
                    >
                      Years
                    </label>
                    <input
                      id={customInputId}
                      type="number"
                      inputMode="numeric"
                      min={CUSTOM_PERIODS_MIN}
                      max={CUSTOM_PERIODS_MAX}
                      step={1}
                      value={customPeriods ?? ""}
                      disabled={disabled}
                      aria-invalid={isCustomSelected && customError !== null}
                      className="ref w-12 rounded-md border border-slate-300 bg-white px-1.5 py-0.5 text-sm tabular-nums focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-500"
                      onFocus={() => {
                        if (!isSelected) {
                          onChange("custom");
                        }
                      }}
                      onChange={(event) => {
                        if (!isSelected) {
                          onChange("custom");
                        }
                        const raw = event.target.value;
                        onCustomPeriodsChange(raw === "" ? null : Number(raw));
                      }}
                    />
                  </span>
                ) : (
                  <span className="ref text-xs text-muted-foreground">
                    {option.periodsLabel}
                  </span>
                )}
              </span>
              <span className="text-xs leading-snug text-muted-foreground">
                {option.summary}
              </span>

              {isCustom && isCustomSelected && customError !== null ? (
                <span role="alert" className="text-xs text-flag">
                  {customError}
                </span>
              ) : null}
            </label>
          );
        })}
      </div>
    </fieldset>
  );
}
