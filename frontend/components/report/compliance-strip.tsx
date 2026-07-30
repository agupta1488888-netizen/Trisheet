/**
 * The compliance strip.
 *
 * What the verification gate found: how many facts the report rests on, how
 * they distribute across the four source tiers, and how many of the figures
 * rendered on the page carry a citation.
 *
 * Every number here is counted by m11 and arrives already computed. The only
 * arithmetic below is bar geometry — turning a count into a width — which is
 * layout, not a reported figure. Coverage is rendered from the string the
 * backend supplies, never recomputed from the ratio.
 */

import { cn } from "@/lib/utils";
import { TIER_NAME } from "@/lib/constants";
import { formatCount } from "@/lib/format";
import type { ComplianceSummary, SourceTier } from "@/lib/types";

const TIERS: readonly SourceTier[] = [1, 2, 3, 4];

const TIER_BAR_CLASS: Readonly<Record<SourceTier, string>> = {
  1: "bg-certified",
  2: "bg-certified/45",
  3: "bg-market",
  4: "bg-flag",
};

const TIER_DOT_CLASS: Readonly<Record<SourceTier, string>> = {
  1: "bg-certified",
  2: "bg-certified/45",
  3: "bg-market",
  4: "bg-flag",
};

function Metric({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "certified" | "flag";
}) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[0.68rem] text-muted-foreground">{label}</span>
      <span
        className={cn(
          "figure text-left text-sm",
          tone === "certified" && "text-certified",
          tone === "flag" && "text-flag",
        )}
      >
        {value}
      </span>
    </div>
  );
}

export function ComplianceStrip({
  compliance,
}: {
  compliance: ComplianceSummary;
}) {
  const total = TIERS.reduce(
    (sum, tier) => sum + compliance.tierCounts[tier],
    0,
  );

  return (
    <section
      aria-label="Verification summary"
      className="border-y border-rule py-4"
    >
      <div className="flex flex-wrap items-start gap-x-10 gap-y-4">
        <Metric label="Facts" value={formatCount(compliance.factCount)} />
        <Metric
          label="Figures cited"
          value={`${formatCount(compliance.citedFigureCount)} of ${formatCount(compliance.figureCount)}`}
        />
        <Metric
          label="Citation coverage"
          value={compliance.coverageDisplay}
          tone={compliance.passed ? "certified" : "flag"}
        />
        <Metric
          label="Verification"
          value={compliance.passed ? "Passed" : "Failed"}
          tone={compliance.passed ? "certified" : "flag"}
        />

        <div className="min-w-56 flex-1">
          <span className="text-[0.68rem] text-muted-foreground">
            Source tiers
          </span>

          <div
            className="mt-1.5 flex h-1.5 w-full overflow-hidden"
            role="img"
            aria-label={TIERS.map(
              (tier) =>
                `${TIER_NAME[tier]}: ${formatCount(compliance.tierCounts[tier])} facts`,
            ).join(", ")}
          >
            {total === 0 ? (
              <span className="w-full bg-rule" />
            ) : (
              TIERS.filter((tier) => compliance.tierCounts[tier] > 0).map(
                (tier) => (
                  <span
                    key={tier}
                    className={cn("h-full", TIER_BAR_CLASS[tier])}
                    style={{
                      width: `${(compliance.tierCounts[tier] / total) * 100}%`,
                    }}
                  />
                ),
              )
            )}
          </div>

          <ul className="mt-2 flex flex-wrap gap-x-5 gap-y-1">
            {TIERS.map((tier) => (
              <li
                key={tier}
                className="flex items-baseline gap-1.5 text-[0.7rem]"
              >
                <span
                  aria-hidden="true"
                  className={cn(
                    "inline-block size-1.5 translate-y-px",
                    TIER_DOT_CLASS[tier],
                  )}
                />
                <span className="text-muted-foreground">
                  {TIER_NAME[tier]}
                </span>
                <span className="figure text-ink">
                  {formatCount(compliance.tierCounts[tier])}
                </span>
              </li>
            ))}
          </ul>
        </div>
      </div>

      <p className="mt-3 text-[0.7rem] text-muted-foreground">
        Financial highlights accept filings and company sources only. Market
        data supports valuation figures and nothing else.
      </p>
    </section>
  );
}
