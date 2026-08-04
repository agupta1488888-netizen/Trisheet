"use client";

/**
 * Analysis depth, as a control that fits inside the search bar.
 *
 * The full `DepthSelector` card grid is the right shape when depth is a
 * primary decision on its own screen. In the hero it is a qualifier on a
 * search, so it collapses to the slot a native `<select>` would occupy —
 * without a native select, because the popup cannot be styled to match.
 *
 * The radio semantics are kept rather than reinvented as a listbox: these are
 * four mutually exclusive choices, native radios carry the arrow-key
 * behaviour and the screen reader announcement for free, and the popup is
 * only a presentation of them.
 */

import { useCallback, useEffect, useId, useRef, useState } from "react";
import { Check, ChevronDown } from "lucide-react";

import { cn } from "@/lib/utils";
import {
  CUSTOM_PERIODS_MAX,
  CUSTOM_PERIODS_MIN,
  DEPTH_OPTIONS,
} from "@/lib/constants";
import type { AnalysisDepth } from "@/lib/types";

export function DepthMenu({
  value,
  onChange,
  customPeriods,
  onCustomPeriodsChange,
  name,
  disabled = false,
}: {
  value: AnalysisDepth;
  onChange: (value: AnalysisDepth) => void;
  customPeriods: number | null;
  onCustomPeriodsChange: (value: number | null) => void;
  name: string;
  disabled?: boolean;
}) {
  const [isOpen, setIsOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const customInputId = useId();
  const menuId = useId();

  const selected = DEPTH_OPTIONS.find((option) => option.value === value);

  const close = useCallback((returnFocus: boolean) => {
    setIsOpen(false);
    if (returnFocus) {
      triggerRef.current?.focus();
    }
  }, []);

  // Dismissal. Pointerdown rather than click so the menu closes on the press
  // that begins an interaction elsewhere, not on the release — otherwise a
  // press on the search field leaves the popup up for a frame.
  useEffect(() => {
    if (!isOpen) {
      return;
    }
    const onPointerDown = (event: PointerEvent) => {
      const container = containerRef.current;
      if (container !== null && event.target instanceof Node) {
        if (!container.contains(event.target)) {
          setIsOpen(false);
        }
      }
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        close(true);
      }
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [isOpen, close]);

  return (
    <div ref={containerRef} className="relative shrink-0">
      <button
        ref={triggerRef}
        type="button"
        disabled={disabled}
        aria-haspopup="true"
        aria-expanded={isOpen}
        aria-controls={isOpen ? menuId : undefined}
        onClick={() => {
          setIsOpen((open) => !open);
        }}
        className={cn(
          "flex items-center gap-1.5 rounded-lg px-3 py-2.5 text-sm font-medium whitespace-nowrap text-white/65",
          "transition-colors hover:bg-white/[0.06] hover:text-white/90 motion-reduce:transition-none",
          "focus-visible:outline focus-visible:outline-1 focus-visible:outline-offset-2 focus-visible:outline-white/40",
          "disabled:cursor-not-allowed disabled:opacity-40",
        )}
      >
        <span>{selected === undefined ? "Depth" : selected.label}</span>
        {value === "custom" && customPeriods !== null ? (
          <span className="ref text-white/40">{customPeriods}y</span>
        ) : selected === undefined ? null : (
          <span className="ref text-white/40">{selected.periodsLabel}</span>
        )}
        <ChevronDown
          aria-hidden="true"
          className={cn(
            "size-3.5 text-white/40 transition-transform motion-reduce:transition-none",
            isOpen && "rotate-180",
          )}
        />
      </button>

      {isOpen ? (
        <div
          id={menuId}
          className="absolute right-0 bottom-full z-30 mb-2 w-72 rounded-xl border border-white/10 bg-[#0f0f12]/95 p-1.5 shadow-2xl shadow-black/60 backdrop-blur-xl"
        >
          <fieldset disabled={disabled}>
            <legend className="sr-only">Analysis depth</legend>
            {DEPTH_OPTIONS.map((option) => {
              const isSelected = option.value === value;
              const isCustom = option.value === "custom";
              return (
                <label
                  key={option.value}
                  className={cn(
                    "flex cursor-pointer items-start gap-2.5 rounded-lg px-2.5 py-2 transition-colors motion-reduce:transition-none",
                    isSelected ? "bg-white/[0.07]" : "hover:bg-white/[0.04]",
                    "has-[input:focus-visible]:outline has-[input:focus-visible]:outline-1 has-[input:focus-visible]:outline-white/40",
                  )}
                >
                  <input
                    type="radio"
                    name={name}
                    value={option.value}
                    checked={isSelected}
                    onChange={() => {
                      onChange(option.value);
                      if (!isCustom) {
                        close(true);
                      }
                    }}
                    className="sr-only"
                  />
                  <Check
                    aria-hidden="true"
                    className={cn(
                      "mt-0.5 size-3.5 shrink-0",
                      isSelected ? "text-white" : "text-transparent",
                    )}
                  />
                  <span className="min-w-0 flex-1">
                    <span className="flex items-baseline gap-2">
                      <span
                        className={cn(
                          "text-sm",
                          isSelected
                            ? "font-medium text-white"
                            : "text-white/80",
                        )}
                      >
                        {option.label}
                      </span>
                      {isCustom ? null : (
                        <span className="ref text-xs text-white/35">
                          {option.periodsLabel}
                        </span>
                      )}
                    </span>
                    <span className="mt-0.5 block text-xs leading-snug text-white/40">
                      {option.summary}
                    </span>

                    {isCustom ? (
                      <span className="mt-2 flex items-center gap-2">
                        <label
                          htmlFor={customInputId}
                          className="text-xs text-white/40"
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
                          className="ref w-16 rounded-md border border-white/12 bg-white/[0.04] px-2 py-1 text-xs tabular-nums text-white focus-visible:outline focus-visible:outline-1 focus-visible:outline-offset-1 focus-visible:outline-white/40"
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
                            onCustomPeriodsChange(
                              raw === "" ? null : Number(raw),
                            );
                          }}
                        />
                        <span className="text-xs text-white/25">
                          {CUSTOM_PERIODS_MIN}–{CUSTOM_PERIODS_MAX}
                        </span>
                      </span>
                    ) : null}
                  </span>
                </label>
              );
            })}
          </fieldset>
        </div>
      ) : null}
    </div>
  );
}
