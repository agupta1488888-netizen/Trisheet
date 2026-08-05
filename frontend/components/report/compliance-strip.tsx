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

import { AlertTriangle, Check } from "lucide-react";

import { cn } from "@/lib/utils";
import { TIER_NAME } from "@/lib/constants";
import { formatCount } from "@/lib/format";
import type { CheckResult, ComplianceSummary, SourceTier } from "@/lib/types";

const TIERS: readonly SourceTier[] = [1, 2, 3, 4];

/**
 * The hierarchy, stated rather than left to be inferred from which tiers
 * happen to have a count above. A reader should be able to see the rule the
 * report was built under without reading the codebase that enforces it.
 */
const HIERARCHY: readonly {
  tier: SourceTier;
  name: string;
  scope: string;
}[] = [
  {
    tier: 1,
    name: "SEC filings",
    scope: "10-K, 10-Q, 8-K, DEF 14A, 20-F, 40-F, 6-K and exhibits",
  },
  {
    tier: 2,
    name: "Company sources",
    scope: "Website, investor presentations, press releases",
  },
  {
    tier: 3,
    name: "Market data",
    scope: "Share price, market capitalisation and multiples only",
  },
  {
    tier: 4,
    name: "News and general web",
    scope: "Never supports a reported figure",
  },
];

const TIER_TEXT_CLASS: Readonly<Record<SourceTier, string>> = {
  1: "text-certified",
  2: "text-certified/70",
  3: "text-market",
  4: "text-flag",
};

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
  passed,
}: {
  label: string;
  value: string;
  tone?: "certified" | "flag";
  /** When set, a check/warning glyph supplements (never replaces) the text. */
  passed?: boolean;
}) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[0.68rem] text-muted-foreground">{label}</span>
      <span
        className={cn(
          "figure flex items-center gap-1.5 text-left text-sm",
          tone === "certified" && "text-certified",
          tone === "flag" && "text-flag",
        )}
      >
        {passed === undefined ? null : passed ? (
          <Check aria-hidden="true" strokeWidth={2} className="size-3.5" />
        ) : (
          <AlertTriangle
            aria-hidden="true"
            strokeWidth={2}
            className="size-3.5"
          />
        )}
        {value}
      </span>
    </div>
  );
}

/**
 * What m11 reconciled, and against what tolerance.
 *
 * The counts above say how much was checked; these say what was checked.
 * Segments against the consolidated total, the balance sheet against itself,
 * the cash flow statement against the change in cash — each with a stated
 * tolerance, because "equal" on a filing that rounds its own figures means
 * "equal within a bound someone chose and wrote down".
 *
 * A check with nothing to run on is reported as such. That is not a pass, and
 * showing it as one would be the most flattering possible lie.
 */
function Reconciliations({ checks }: { checks: readonly CheckResult[] }) {
  if (checks.length === 0) {
    return null;
  }

  return (
    <div className="mt-4 border-t border-rule pt-3">
      <span className="text-[0.68rem] text-muted-foreground">
        Reconciliations
      </span>
      <ul className="mt-1.5 flex flex-col gap-1">
        {checks.map((check) => (
          <li
            key={check.check}
            className="flex items-center gap-1.5 text-[0.7rem]"
          >
            <span
              className={cn(
                "ref flex items-center gap-1",
                check.examined === 0
                  ? "text-muted-foreground"
                  : check.passed
                    ? "text-certified"
                    : "text-flag",
              )}
            >
              {check.examined === 0 ? null : check.passed ? (
                <Check aria-hidden="true" strokeWidth={2} className="size-3" />
              ) : (
                <AlertTriangle
                  aria-hidden="true"
                  strokeWidth={2}
                  className="size-3"
                />
              )}
              {check.examined === 0
                ? "Not applicable"
                : check.passed
                  ? "Reconciled"
                  : "Discrepancy"}
            </span>
            <span className="text-muted-foreground">{check.description}</span>
          </li>
        ))}
      </ul>
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
          passed={compliance.passed}
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

      <Reconciliations checks={compliance.checks} />

      <div className="mt-4 border-t border-rule pt-3">
        <span className="text-[0.68rem] text-muted-foreground">
          Source hierarchy
        </span>
        <ul className="mt-1.5 flex flex-wrap gap-x-6 gap-y-1">
          {HIERARCHY.map((entry) => (
            <li key={entry.tier} className="text-[0.7rem]">
              <span className={cn("ref mr-1.5", TIER_TEXT_CLASS[entry.tier])}>
                Tier {entry.tier}
              </span>
              <span className="text-ink">{entry.name}</span>
              <span className="text-muted-foreground"> — {entry.scope}</span>
            </li>
          ))}
        </ul>
      </div>

      <p className="mt-3 text-[0.7rem] text-muted-foreground">
        Financial highlights accept filings and company sources only. Market
        data supports valuation figures and nothing else. Tier 3 and tier 4 are
        refused when a figure is written, not filtered when it is rendered.
      </p>
    </section>
  );
}
