import { notFound } from "next/navigation";

import {
  FAILED_REPORT_MESSAGE,
  PROGRESS_SCRIPT,
  PROGRESS_SCRIPT_DEGRADED,
  PROGRESS_SCRIPT_FAILED,
  type ProgressFrame,
} from "@/lib/mock/progress";
import { PreviewNotice } from "@/components/preview/preview-notice";
import { ScriptedStepFeed } from "@/components/preview/scripted-step-feed";

/** The progress feed, playing a scripted run. */

interface ScriptRoute {
  script: readonly ProgressFrame[];
  ticker: string;
  what: string;
}

const SCRIPTS: Readonly<Record<string, ScriptRoute>> = {
  nominal: {
    script: PROGRESS_SCRIPT,
    ticker: "AAPL",
    what: "A nominal pipeline run.",
  },
  degraded: {
    script: PROGRESS_SCRIPT_DEGRADED,
    ticker: "SAP",
    what: "A run with market data unreachable.",
  },
  failed: {
    script: PROGRESS_SCRIPT_FAILED,
    ticker: "ZZZZ",
    what: "A run that cannot continue.",
  },
};

export function generateStaticParams() {
  return Object.keys(SCRIPTS).map((script) => ({ script }));
}

export default async function PreviewProgress({
  params,
}: {
  params: Promise<{ script: string }>;
}) {
  const { script: slug } = await params;
  const route = SCRIPTS[slug];

  if (route === undefined) {
    notFound();
  }

  return (
    <>
      <PreviewNotice what={route.what} />
      <main id="main" className="mx-auto max-w-3xl px-5 py-14 sm:px-8">
        <ScriptedStepFeed
          ticker={route.ticker}
          script={route.script}
          errorMessage={FAILED_REPORT_MESSAGE}
        />
      </main>
    </>
  );
}
