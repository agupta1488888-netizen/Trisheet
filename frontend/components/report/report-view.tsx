"use client";

/**
 * The report.
 *
 * Three columns on a wide screen: the section sidebar, the document, and the
 * provenance rail — both rails are peers of the content, not an appendix to
 * it, which is why the grid is declared here rather than inside either
 * component. Below `lg` there is no room for either column: the sidebar
 * collapses into the horizontal nav under the header, and the provenance
 * rail becomes a docked panel (handled inside `ProvenanceRail` itself).
 *
 * The seven sections render in brief order, exactly as `SECTION_ORDER`
 * declares. Sections are not reordered by how much data they happen to have.
 */

import { useMemo } from "react";
import Link from "next/link";

import { SECTION_NAV_LABEL } from "@/lib/constants";
import { buildSourceIndex } from "@/lib/provenance";
import { SECTION_ORDER, type ReportDocument } from "@/lib/types";
import { ArtifactDownloads } from "@/components/report/artifact-downloads";
import { ChatPanel } from "@/components/report/chat-panel";
import { ComplianceStrip } from "@/components/report/compliance-strip";
import { ProvenanceProvider } from "@/components/report/provenance-context";
import { ProvenanceRail } from "@/components/report/provenance-rail";
import { ReportHeader } from "@/components/report/report-header";
import { ReportSection } from "@/components/report/report-section";
import { SectionSidebar } from "@/components/report/section-sidebar";
import { SourceNotes } from "@/components/report/source-notes";

/** Mobile/tablet fallback: the sidebar takes over at `lg`, see SectionSidebar. */
function SectionNav({ ids }: { ids: readonly string[] }) {
  return (
    <nav aria-label="Sections" className="border-b border-rule py-3 lg:hidden">
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
      <div className="mx-auto grid max-w-7xl grid-cols-1 gap-x-10 px-5 py-10 pb-40 sm:px-8 lg:grid-cols-[9rem_minmax(0,1fr)_17rem] lg:pb-16">
        <SectionSidebar ids={sections.map((section) => section.id)} />

        <main id="report" className="min-w-0">
          <Link
            href="/"
            className="text-sm text-certified underline underline-offset-4 hover:text-ink"
          >
            ← Back to search
          </Link>

          <div className="mt-6">
            <ReportHeader
              company={document.company}
              depth={document.depth}
              completedAt={document.report.completedAt}
            />
          </div>

          <div className="mt-4">
            <ArtifactDownloads artifacts={document.artifacts} />
          </div>

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

          {/* After every filed section, never among them. These are appended
              to the record rather than part of it, and renders nothing when
              no link was supplied. */}
          <SourceNotes notes={document.sourceNotes} />
        </main>

        <ProvenanceRail index={index} />
      </div>

      <ChatPanel reportId={document.report.id} />
    </ProvenanceProvider>
  );
}
