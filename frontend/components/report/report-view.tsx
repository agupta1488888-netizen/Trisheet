"use client";

/**
 * The report.
 *
 * Two columns on a wide screen: the document, and the provenance rail beside
 * it. The rail is a peer of the content, not an appendix to it — that is why
 * the grid is declared here rather than inside either component.
 *
 * The seven sections render in brief order, exactly as `SECTION_ORDER`
 * declares. Sections are not reordered by how much data they happen to have.
 */

import { useMemo } from "react";

import { SECTION_NAV_LABEL } from "@/lib/constants";
import { buildSourceIndex } from "@/lib/provenance";
import { SECTION_ORDER, type ReportDocument } from "@/lib/types";
import { ComplianceStrip } from "@/components/report/compliance-strip";
import { ProvenanceProvider } from "@/components/report/provenance-context";
import { ProvenanceRail } from "@/components/report/provenance-rail";
import { ReportHeader } from "@/components/report/report-header";
import { ReportSection } from "@/components/report/report-section";

function SectionNav({ ids }: { ids: readonly string[] }) {
  return (
    <nav aria-label="Sections" className="border-b border-rule py-3">
      <ul className="flex flex-wrap gap-x-5 gap-y-1">
        {ids.map((id) => (
          <li key={id}>
            <a
              href={`#${id}`}
              className="text-xs text-muted-foreground underline-offset-4 hover:text-ink hover:underline focus-visible:text-ink focus-visible:underline"
            >
              {SECTION_NAV_LABEL[id as keyof typeof SECTION_NAV_LABEL] ?? id}
            </a>
          </li>
        ))}
      </ul>
    </nav>
  );
}

export function ReportView({ document }: { document: ReportDocument }) {
  const index = useMemo(
    () => buildSourceIndex(document.facts, document.filings),
    [document.facts, document.filings],
  );

  // Render in the declared order, and only sections the backend supplied.
  const sections = SECTION_ORDER.map((id) =>
    document.sections.find((section) => section.id === id),
  ).filter((section) => section !== undefined);

  return (
    <ProvenanceProvider index={index}>
      <div className="mx-auto grid max-w-7xl grid-cols-1 gap-x-12 px-5 py-10 pb-28 sm:px-8 lg:grid-cols-[minmax(0,1fr)_17rem] lg:pb-16">
        <main id="report" className="min-w-0">
          <ReportHeader
            company={document.company}
            depth={document.depth}
            completedAt={document.report.completedAt}
          />

          <div className="mt-8">
            <ComplianceStrip compliance={document.compliance} />
          </div>

          <SectionNav ids={sections.map((section) => section.id)} />

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
        </main>

        <ProvenanceRail index={index} />
      </div>
    </ProvenanceProvider>
  );
}
