"use client";

/**
 * The ticker field.
 *
 * A single input with an attached listbox, following the ARIA combobox
 * pattern: arrow keys move through matches, Enter takes the active one,
 * Escape closes the list, and the active option is announced through
 * `aria-activedescendant` rather than by moving focus. Typing a ticker and
 * pressing Enter without ever opening the list also works — the field accepts
 * free text, because the resolver, not the browser, decides what a query means.
 *
 * Suggestions are fetched through the `search` prop so this component has no
 * opinion about where they come from. Requests are debounced and stale
 * responses are discarded.
 */

import { useCallback, useEffect, useId, useRef, useState } from "react";
import { Search } from "lucide-react";

import { cn } from "@/lib/utils";
import {
  AUTOCOMPLETE_DEBOUNCE_MS,
  AUTOCOMPLETE_MAX_RESULTS,
  AUTOCOMPLETE_MIN_CHARS,
} from "@/lib/constants";
import type { FilerType, TickerSuggestion } from "@/lib/types";

/** The annual form a filer type files, shown as a hint beside foreign filers. */
const FORM_HINT: Readonly<Record<FilerType, string | null>> = {
  domestic: null,
  foreign: "20-F",
  canadian: "40-F",
};

export interface TickerComboboxProps {
  value: string;
  onValueChange: (value: string) => void;
  /** Called when a suggestion is taken, so the form can submit immediately. */
  onSelect: (suggestion: TickerSuggestion) => void;
  search: (query: string) => Promise<readonly TickerSuggestion[]>;
  inputId: string;
  disabled?: boolean;
}

export function TickerCombobox({
  value,
  onValueChange,
  onSelect,
  search,
  inputId,
  disabled = false,
}: TickerComboboxProps) {
  const listboxId = useId();
  const [suggestions, setSuggestions] = useState<readonly TickerSuggestion[]>(
    [],
  );
  const [isOpen, setIsOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);
  const containerRef = useRef<HTMLDivElement>(null);
  // Incremented per request; a response whose id is stale is dropped.
  const requestRef = useRef(0);

  useEffect(() => {
    const query = value.trim();
    if (query.length < AUTOCOMPLETE_MIN_CHARS) {
      setSuggestions([]);
      setActiveIndex(-1);
      return;
    }

    const requestId = requestRef.current + 1;
    requestRef.current = requestId;

    const timer = window.setTimeout(() => {
      void search(query).then((results) => {
        if (requestRef.current !== requestId) {
          return;
        }
        setSuggestions(results.slice(0, AUTOCOMPLETE_MAX_RESULTS));
        setActiveIndex(-1);
      });
    }, AUTOCOMPLETE_DEBOUNCE_MS);

    return () => {
      window.clearTimeout(timer);
    };
  }, [value, search]);

  // A click outside closes the list without disturbing what was typed.
  useEffect(() => {
    if (!isOpen) {
      return;
    }
    const onPointerDown = (event: PointerEvent) => {
      if (
        containerRef.current !== null &&
        event.target instanceof Node &&
        !containerRef.current.contains(event.target)
      ) {
        setIsOpen(false);
      }
    };
    window.addEventListener("pointerdown", onPointerDown);
    return () => {
      window.removeEventListener("pointerdown", onPointerDown);
    };
  }, [isOpen]);

  const take = useCallback(
    (suggestion: TickerSuggestion) => {
      onValueChange(suggestion.ticker);
      setIsOpen(false);
      setActiveIndex(-1);
      onSelect(suggestion);
    },
    [onSelect, onValueChange],
  );

  const onKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    const hasList = isOpen && suggestions.length > 0;

    if (event.key === "ArrowDown") {
      event.preventDefault();
      if (!isOpen) {
        setIsOpen(true);
        return;
      }
      setActiveIndex((current) =>
        suggestions.length === 0 ? -1 : (current + 1) % suggestions.length,
      );
      return;
    }

    if (event.key === "ArrowUp") {
      event.preventDefault();
      setActiveIndex((current) =>
        suggestions.length === 0
          ? -1
          : (current - 1 + suggestions.length) % suggestions.length,
      );
      return;
    }

    if (event.key === "Escape") {
      if (isOpen) {
        event.preventDefault();
        setIsOpen(false);
        setActiveIndex(-1);
      }
      return;
    }

    if (event.key === "Enter" && hasList && activeIndex >= 0) {
      const suggestion = suggestions[activeIndex];
      if (suggestion !== undefined) {
        // Enter takes the highlighted match instead of submitting raw text.
        event.preventDefault();
        take(suggestion);
      }
      return;
    }

    if (event.key === "Tab" && isOpen) {
      setIsOpen(false);
    }
  };

  const showList = isOpen && suggestions.length > 0;
  const activeId =
    activeIndex >= 0 && suggestions[activeIndex] !== undefined
      ? `${listboxId}-${activeIndex}`
      : undefined;

  return (
    <div ref={containerRef} className="relative">
      <Search
        aria-hidden="true"
        strokeWidth={2}
        className="pointer-events-none absolute top-1/2 left-3.5 size-4 -translate-y-1/2 text-white/30"
      />
      <input
        id={inputId}
        type="text"
        role="combobox"
        aria-expanded={showList}
        aria-controls={listboxId}
        aria-autocomplete="list"
        aria-activedescendant={activeId}
        aria-describedby={`${inputId}-hint`}
        autoComplete="off"
        autoCapitalize="characters"
        spellCheck={false}
        disabled={disabled}
        placeholder="Ticker or company name"
        value={value}
        onChange={(event) => {
          onValueChange(event.target.value);
          setIsOpen(true);
        }}
        onFocus={() => {
          if (suggestions.length > 0) {
            setIsOpen(true);
          }
        }}
        onKeyDown={onKeyDown}
        className={cn(
          "ref h-11 w-full rounded-xl border-0 bg-transparent py-0 pr-3 pl-10 text-[15px] text-white",
          "placeholder:font-sans placeholder:text-[15px] placeholder:text-white/30",
          "outline-none focus-visible:outline-none",
          "disabled:cursor-not-allowed disabled:opacity-50",
          "motion-reduce:transition-none",
        )}
      />

      {/* Result count, announced without stealing focus. */}
      <p className="sr-only" aria-live="polite">
        {showList
          ? `${suggestions.length} ${suggestions.length === 1 ? "match" : "matches"}`
          : ""}
      </p>

      <ul
        id={listboxId}
        role="listbox"
        aria-label="Matching companies"
        hidden={!showList}
        className="absolute inset-x-0 top-full z-30 mt-3 max-h-72 overflow-y-auto rounded-xl border border-white/10 bg-[#0f0f12]/95 p-1.5 shadow-2xl shadow-black/60 backdrop-blur-xl"
      >
        {suggestions.map((suggestion, position) => {
          const hint = suggestion.filerType
            ? FORM_HINT[suggestion.filerType]
            : null;
          return (
            <li
              key={suggestion.cik}
              id={`${listboxId}-${position}`}
              role="option"
              aria-selected={position === activeIndex}
              onPointerDown={(event) => {
                // Keep focus in the input so blur does not close the list
                // before the click lands.
                event.preventDefault();
                take(suggestion);
              }}
              onMouseEnter={() => {
                setActiveIndex(position);
              }}
              className={cn(
                "flex cursor-pointer items-baseline gap-3 rounded-xl px-3.5 py-2.5",
                position === activeIndex && "bg-white/[0.07]",
              )}
            >
              <span className="ref w-16 shrink-0 text-sm text-white">
                {suggestion.ticker}
              </span>
              <span className="min-w-0 flex-1 truncate text-sm text-white/80">
                {suggestion.name}
              </span>
              {hint === null ? null : (
                <span className="ref shrink-0 text-[0.68rem] text-white/35">
                  {hint}
                </span>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
