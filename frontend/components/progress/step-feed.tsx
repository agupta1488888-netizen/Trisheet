"use client";

/**
 * The progress feed.
 *
 * Not a spinner and not a percentage. Each pipeline step gets a line, and when
 * it settles the line states what it actually produced: 31 filings, 142
 * figures, 5 peers. A reader waiting on a report should be able to tell from
 * this feed whether the run is going well, which no progress bar can express.
 *
 * A skipped step is not a failure. Only SEC EDGAR is a hard dependency, so
 * "market data unavailable" is reported in the same voice as a success, with
 * the consequence spelled out.
 */

import { cn } from "@/lib/utils";
import { formatCount, formatFeedTime } from "@/lib/format";
import type { ProgressStep, ReportStatus, StepState } from "@/lib/types";

const STATE_LABEL: Readonly<Record<StepState, string>> = {
  pending: "waiting",
  running: "running",
  done: "done",
  skipped: "skipped",
  failed: "failed",
};

const STATE_CLASS: Readonly<Record<StepState, string>> = {
  pending: "text-muted-foreground",
  running: "text-ink",
  done: "text-certified",
  skipped: "text-market",
  failed: "text-flag",
};

const STATUS_LABEL: Readonly<Record<ReportStatus, string>> = {
  queued: "Queued",
  resolving: "Resolving",
  extracting: "Extracting",
  analysing: "Analysing",
  writing: "Writing",
  verifying: "Verifying",
  complete: "Complete",
  failed: "Stopped",
};

function StepLine({ step }: { step: ProgressStep }) {
  return (
    <li
      data-module={step.module}
      data-state={step.state}
      className={cn(
        "grid grid-cols-[4.5rem_2.5rem_1fr] items-baseline gap-x-3 gap-y-1 border-b border-rule py-2",
        "sm:grid-cols-[5rem_2.5rem_1fr_9rem_4.5rem]",
      )}
    >
      <span className="figure text-left text-[0.7rem] text-muted-foreground">
        {formatFeedTime(step.at)}
      </span>

      <span
        className={cn(
          "ref text-[0.7rem]",
          step.state === "running"
            ? "animate-pulse text-ink motion-reduce:animate-none"
            : "text-muted-foreground",
        )}
      >
        {step.module}
      </span>

      <span className="col-span-1 text-sm text-ink">
        {step.label}
        {step.detail === null ? null : (
          <span
            className={cn(
              "mt-0.5 block text-xs leading-snug",
              step.state === "failed" ? "text-flag" : "text-muted-foreground",
            )}
          >
            {step.detail}
          </span>
        )}
      </span>

      <span className="figure col-start-3 text-left text-xs text-muted-foreground sm:col-start-4 sm:text-right">
        {step.count === null
          ? ""
          : `${formatCount(step.count)}${step.countLabel === null ? "" : ` ${step.countLabel}`}`}
      </span>

      <span
        className={cn(
          "ref col-start-3 text-left text-[0.7rem] sm:col-start-5 sm:text-right",
          STATE_CLASS[step.state],
        )}
      >
        {STATE_LABEL[step.state]}
      </span>
    </li>
  );
}

export function StepFeed({
  ticker,
  status,
  steps,
  errorMessage,
}: {
  ticker: string;
  status: ReportStatus;
  steps: readonly ProgressStep[];
  errorMessage: string | null;
}) {
  const settled = steps.filter(
    (step) => step.state === "done" || step.state === "skipped",
  ).length;

  return (
    <div>
      <header className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1">
        <h1 className="text-3xl text-ink">
          <span className="ref text-certified">{ticker}</span>
        </h1>
        <p className="ref text-sm text-muted-foreground">
          {STATUS_LABEL[status]}
          <span className="text-muted-foreground">
            {" · "}
            {settled} of {steps.length} steps
          </span>
        </p>
      </header>

      <p className="mt-2 max-w-prose text-sm text-muted-foreground">
        Reading filings from SEC EDGAR. Each step reports what it found.
      </p>

      {/* The feed is a live region so a screen reader hears each step settle
          without the page moving focus. */}
      <ol
        aria-live="polite"
        aria-relevant="additions text"
        className="mt-8 border-t border-rule"
      >
        {steps.map((step) => (
          <StepLine key={step.module} step={step} />
        ))}
      </ol>

      {status === "failed" && errorMessage !== null ? (
        <section
          aria-labelledby="run-failed-heading"
          className="mt-8 border-t-2 border-t-flag pt-4"
        >
          <h2 id="run-failed-heading" className="text-sm font-medium text-flag">
            This report could not be built
          </h2>
          <p className="mt-2 max-w-prose text-sm leading-relaxed text-muted-foreground">
            {errorMessage}
          </p>
        </section>
      ) : null}
    </div>
  );
}
