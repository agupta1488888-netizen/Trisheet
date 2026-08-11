"use client";

/**
 * The valuation page.
 *
 * Its own destination, reached from the report's 8th nav entry rather than a
 * scroll anchor: what follows from the filings under stated assumptions,
 * next to what the filings themselves say, so a reader can hold both at
 * once instead of scrolling between them.
 *
 * Three columns on a wide screen: the workbench, the report it rests on, and
 * the provenance rail — the same rail the report page uses, so a source card
 * raised here is the same card that would raise there. Below `lg` there is no
 * room for three columns: the workbench renders above the report, and the
 * rail docks to the bottom of the viewport (handled inside `ProvenanceRail`
 * itself, same as the report page).
 */

import { useMemo, useState } from "react";

import { WORKBENCH_NAV_LABEL } from "@/lib/constants";
import { buildSourceIndex } from "@/lib/provenance";
import {
  SECTION_ORDER,
  type Fact,
  type ReportDocument,
  type ValuationAssumptions,
  type ValuationResponse,
} from "@/lib/types";
import { ArtifactDownloads } from "@/components/report/artifact-downloads";
import { ComplianceStrip } from "@/components/report/compliance-strip";
import { ProvenanceProvider } from "@/components/report/provenance-context";
import { ProvenanceRail } from "@/components/report/provenance-rail";
import { ReportHeader } from "@/components/report/report-header";
import { ReportSection } from "@/components/report/report-section";
import { SourceNotes } from "@/components/report/source-notes";
import { ValuationWorkbench } from "@/components/report/workbench/valuation-workbench";
import { SiteHeader } from "@/components/chrome/site-header";

export function ValuationPageView({
  document,
  valuation,
  assumptions,
}: {
  document: ReportDocument;
  valuation: ValuationResponse | null;
  assumptions: ValuationAssumptions;
}) {
  // Same reasoning as the report page: the workbench can name a fact the
  // report's own facts did not carry, and the rail needs it too.
  const [valuationInputs, setValuationInputs] = useState<readonly Fact[]>(
    valuation?.inputs ?? [],
  );

  const index = useMemo(
    () =>
      buildSourceIndex(
        [...document.facts, ...valuationInputs],
        document.filings,
      ),
    [document.facts, valuationInputs, document.filings],
  );

  const sections = SECTION_ORDER.map((id) =>
    document.sections.find((section) => section.id === id),
  ).filter((section) => section !== undefined);

  return (
    <ProvenanceProvider index={index}>
      <SiteHeader
        variant="paper"
        backHref={`/r/${document.report.id}`}
        backLabel="Back to report"
      />

      <div className="mx-auto max-w-7xl px-5 sm:px-8">
        <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-3 border-b border-rule py-6">
          <div>
            <p className="ref text-sm text-certified">
              {document.company.ticker}
            </p>
            <h1 className="mt-1 font-display text-2xl text-ink">
              {WORKBENCH_NAV_LABEL}
            </h1>
          </div>
          <ArtifactDownloads artifacts={document.artifacts} />
        </div>
      </div>

      <div className="mx-auto grid max-w-7xl grid-cols-1 gap-x-10 px-5 py-10 pb-40 sm:px-8 lg:grid-cols-[27rem_minmax(0,1fr)_17rem] lg:items-start lg:pb-16">
        <div className="min-w-0 lg:sticky lg:top-8 lg:max-h-[calc(100vh-4rem)] lg:self-start lg:overflow-y-auto lg:pb-8">
          <ValuationWorkbench
            reportId={document.report.id}
            initial={valuation}
            initialAssumptions={assumptions}
            onInputsChange={(response) => {
              setValuationInputs(response.inputs);
            }}
          />
        </div>

        <main className="min-w-0 border-t border-rule pt-8 lg:sticky lg:top-8 lg:max-h-[calc(100vh-4rem)] lg:self-start lg:overflow-y-auto lg:border-t-0 lg:pt-0 lg:pb-8">
          <div>
            <ReportHeader
              company={document.company}
              depth={document.depth}
              completedAt={document.report.completedAt}
            />
          </div>

          <div className="mt-8">
            <ComplianceStrip compliance={document.compliance} />
          </div>

          <div className="mt-10 space-y-12">
            {sections.map((section, position) => (
              <ReportSection
                key={section.id}
                section={section}
                position={position + 1}
                charts={document.charts}
              />
            ))}
          </div>

          <SourceNotes notes={document.sourceNotes} />
        </main>

        <ProvenanceRail index={index} />
      </div>
    </ProvenanceProvider>
  );
}
