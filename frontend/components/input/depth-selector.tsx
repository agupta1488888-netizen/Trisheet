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
      <legend className="text-[0.68rem] text-muted-foreground">
        Analysis depth
      </legend>

      <div className="mt-2 grid grid-cols-1 border-t border-rule sm:grid-cols-3 sm:border-t-0">
        {DEPTH_OPTIONS.map((option) => {
          const isSelected = option.value === value;
          return (
            <label
              key={option.value}
              className={cn(
                "group flex cursor-pointer flex-col gap-1 border-b border-rule px-0 py-3 sm:border-b-0 sm:border-t-2 sm:pr-6",
                isSelected
                  ? "sm:border-t-certified"
                  : "sm:border-t-rule sm:hover:border-t-ink-muted",
                "has-[input:focus-visible]:outline-2 has-[input:focus-visible]:outline-offset-2 has-[input:focus-visible]:outline-certified",
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
                    isSelected ? "font-medium text-ink" : "text-muted-foreground",
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
