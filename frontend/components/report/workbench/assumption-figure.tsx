"use client";

/**
 * A projected figure, and the label that says it is one.
 *
 * The distinction from a filed figure is deliberately structural rather than
 * decorative. A filed figure carries a superscript marker that links to a
 * reference card in the rail; this carries a dagger that resolves to nothing,
 * because there is nothing to resolve to. The absence of a source card is the
 * evidence, and it cannot be faked by styling.
 *
 * What does not lapse: the figure stays monospace, tabular and right-aligned.
 * That part of the identity holds everywhere in the report, including here.
 */

import { cn } from "@/lib/utils";
import { NOT_DISCLOSED } from "@/lib/constants";
import type { AssumptionOut, ValuationFigure } from "@/lib/types";

/**
 * A projected amount.
 *
 * `note` is what the dagger says on hover, and it is required: a projection
 * with nothing to say about where it came from should not render at all.
 */
export function AssumptionFigure({
  figure,
  note,
  className,
}: {
  figure: ValuationFigure | null;
  note: string;
  className?: string;
}) {
  if (figure === null) {
    return (
      <span className={cn("figure text-muted-foreground", className)}>
        {NOT_DISCLOSED}
      </span>
    );
  }

  return (
    <span className={cn("figure text-muted-foreground", className)}>
      <abbr
        title={note}
        className="cursor-help no-underline decoration-dotted underline-offset-2"
      >
        {figure.display}
      </abbr>
      <span aria-hidden="true" className="ml-0.5 text-[0.62rem] align-super">
        †
      </span>
    </span>
  );
}

/** A rate, rendered the way the assumption that carries it describes itself. */
export function AssumptionRate({
  assumption,
  className,
}: {
  assumption: AssumptionOut | null;
  className?: string;
}) {
  if (assumption === null) {
    return (
      <span className={cn("figure text-muted-foreground", className)}>
        {NOT_DISCLOSED}
      </span>
    );
  }

  return (
    <span className={cn("figure text-muted-foreground", className)}>
      <abbr
        title={assumption.note}
        className="cursor-help no-underline decoration-dotted underline-offset-2"
      >
        {assumption.display}
      </abbr>
      <span aria-hidden="true" className="ml-0.5 text-[0.62rem] align-super">
        †
      </span>
    </span>
  );
}
