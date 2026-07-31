"use client";

/**
 * The progress screen.
 *
 * Wires the live feed to the step list and refreshes the route once the run
 * settles, so a completed report replaces this screen without the reader
 * having to do anything.
 *
 * When Realtime is unavailable the feed says so plainly rather than sitting
 * there looking broken. The run is still going; we simply cannot narrate it
 * step by step.
 */

import { useEffect } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import { useReportProgress } from "@/hooks/use-report-progress";
import type { ReportStatus } from "@/lib/types";
import { StepFeed } from "@/components/progress/step-feed";

export function ProgressScreen({
  reportId,
  ticker,
  initialStatus,
  initialErrorMessage,
}: {
  reportId: string;
  ticker: string;
  initialStatus: ReportStatus;
  initialErrorMessage: string | null;
}) {
  const router = useRouter();
  const { status, steps, errorMessage, isLive } = useReportProgress(reportId, {
    status: initialStatus,
    errorMessage: initialErrorMessage,
  });

  useEffect(() => {
    if (status === "complete") {
      router.refresh();
    }
  }, [status, router]);

  return (
    <main
      id="main"
      className="mx-auto min-h-screen max-w-3xl px-5 py-16 sm:px-8"
    >
      <Link
        href="/"
        className="text-sm text-certified underline underline-offset-4 hover:text-ink"
      >
        ← Back to search
      </Link>

      <div className="mt-6">
        <StepFeed
          ticker={ticker}
          status={status}
          steps={steps}
          errorMessage={errorMessage}
        />

        {isLive || status === "failed" ? null : (
          <p className="mt-6 border-l-2 border-l-market pl-4 text-xs leading-relaxed text-muted-foreground">
            The live feed is not connected, so this page is checking for
            progress every couple of seconds instead of listing each step as
            it finishes. The report itself is unaffected.
          </p>
        )}
      </div>
    </main>
  );
}
