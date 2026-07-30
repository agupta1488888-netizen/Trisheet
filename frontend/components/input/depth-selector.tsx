"use client";

/**
 * Analysis depth.
 *
 * A radio group rather than a dropdown: there are three options, they are
 * mutually exclusive, and the difference between them is worth reading before
 * choosing. Native radio inputs carry the keyboard behaviour and the screen
 * reader semantics for free — the visible control is the label.
 */

import { cn } from "@/lib/utils";
import { DEPTH_OPTIONS } from "@/lib/constants";
import type { AnalysisDepth } from "@/lib/types";

export function DepthSelector({
  value,
  onChange,
  name,
  disabled = false,
}: {
  value: AnalysisDepth;
  onChange: (value: AnalysisDepth) => void;
  name: string;
  disabled?: boolean;
}) {
  return (
    <fieldset disabled={disabled} className="min-w-0">
      <legend className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">
        Analysis depth
      </legend>

      <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-3">
        {DEPTH_OPTIONS.map((option) => {
          const isSelected = option.value === value;
          return (
            <label
              key={option.value}
              className={cn(
                "group flex cursor-pointer flex-col gap-1.5 rounded-xl border-2 p-4 transition-colors motion-reduce:transition-none",
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
                    "text-sm",
                    isSelected
                      ? "font-semibold text-emerald-800"
                      : "font-medium text-ink",
                  )}
                >
                  {option.label}
                </span>
                <span className="ref text-[0.68rem] text-muted-foreground">
                  {option.periodsLabel}
                </span>
              </span>
              <span className="text-xs leading-snug text-muted-foreground">
                {option.summary}
              </span>
            </label>
          );
        })}
      </div>
    </fieldset>
  );
}
