"use client";

/**
 * The section sidebar — a persistent table of contents beside the report.
 *
 * On a wide screen it sits to the left of the document as a sticky column,
 * mirroring the provenance rail on the right: a hairline rule, no cards, no
 * shadows. Clicking a label jumps to that section; the label for whichever
 * section is currently in view is marked, so the reader always knows where
 * they are in a long report.
 */

import { useEffect, useState } from "react";

import { SECTION_NAV_LABEL } from "@/lib/constants";
import { cn } from "@/lib/utils";

export function SectionSidebar({ ids }: { ids: readonly string[] }) {
  const [activeId, setActiveId] = useState<string | null>(ids[0] ?? null);

  useEffect(() => {
    const elements = ids
      .map((id) => document.getElementById(id))
      .filter((el): el is HTMLElement => el !== null);

    if (elements.length === 0) {
      return;
    }

    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((entry) => entry.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);

        const nearest = visible[0];
        if (nearest !== undefined) {
          setActiveId(nearest.target.id);
        }
      },
      { rootMargin: "-15% 0px -70% 0px", threshold: 0 },
    );

    elements.forEach((el) => {
      observer.observe(el);
    });

    return () => {
      observer.disconnect();
    };
  }, [ids]);

  return (
    <nav
      aria-label="Sections"
      className="hidden lg:sticky lg:top-8 lg:block lg:max-h-[calc(100vh-4rem)] lg:self-start lg:overflow-y-auto lg:pb-8"
    >
      <ul className="space-y-0.5 border-l border-rule">
        {ids.map((id) => {
          const isActive = id === activeId;

          return (
            <li key={id}>
              <a
                href={`#${id}`}
                aria-current={isActive ? "location" : undefined}
                className={cn(
                  "-ml-px block border-l py-1.5 pl-3 text-sm transition-colors motion-reduce:transition-none",
                  isActive
                    ? "border-l-certified text-ink"
                    : "border-l-transparent text-muted-foreground hover:border-l-rule hover:text-ink",
                )}
              >
                {SECTION_NAV_LABEL[id as keyof typeof SECTION_NAV_LABEL] ?? id}
              </a>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
