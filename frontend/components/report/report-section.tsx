"use client";

/**
 * One section of the report.
 *
 * The section decides what it contains — prose, tables, a timeline, risks —
 * and which chart belongs beside it. A section that could not be built states
 * why, in the interface's voice, and the rest of the report is unaffected:
 * only SEC EDGAR is a hard dependency.
 */

import type { ReportCharts, ReportSection as SectionModel } from "@/lib/types";
import { CashFlowChart } from "@/components/charts/cash-flow-chart";
import { PeerValuationChart } from "@/components/charts/peer-valuation-chart";
import { RevenueMarginChart } from "@/components/charts/revenue-margin-chart";
import { SegmentMixChart } from "@/components/charts/segment-mix-chart";
import { DevelopmentsTimeline } from "@/components/report/developments-timeline";
import { FigureTable } from "@/components/report/figure-table";
import { Prose } from "@/components/report/prose";
import { RiskList } from "@/components/report/risk-list";

/**
 * Which chart sits in which section. One place, so a chart cannot end up
 * rendered twice or in a section it does not belong to.
 */
function SectionCharts({
  sectionId,
  charts,
}: {
  sectionId: SectionModel["id"];
  charts: ReportCharts;
}) {
  if (sectionId === "business" && charts.segmentMix !== null) {
    return <SegmentMixChart series={charts.segmentMix} />;
  }
  if (sectionId === "financials" && charts.revenueMargin !== null) {
    return <RevenueMarginChart series={charts.revenueMargin} />;
  }
  if (sectionId === "analysis" && charts.cashFlow !== null) {
    return <CashFlowChart series={charts.cashFlow} />;
  }
  if (sectionId === "peers" && charts.peerValuation !== null) {
    return <PeerValuationChart series={charts.peerValuation} />;
  }
  return null;
}

export function ReportSection({
  section,
  position,
  charts,
}: {
  section: SectionModel;
  /** 1-based, rendered beside the heading the way a brief numbers sections. */
  position: number;
  charts: ReportCharts;
}) {
  return (
    <section
      id={section.id}
      aria-labelledby={`${section.id}-heading`}
      className="scroll-mt-8 border-t border-rule pt-8 first:border-t-0 first:pt-0"
    >
      <div className="flex items-baseline gap-3">
        <span
          aria-hidden="true"
          className="figure text-xs text-muted-foreground"
        >
          {position}
        </span>
        <h2 id={`${section.id}-heading`} className="text-2xl text-ink">
          {section.title}
        </h2>
      </div>

      {section.unavailableReason !== null ? (
        <p className="mt-4 max-w-prose border-l-2 border-l-market pl-4 text-sm leading-relaxed text-muted-foreground">
          {section.unavailableReason}
        </p>
      ) : (
        <>
          <Prose blocks={section.prose} />

          {section.tables.map((table) => (
            <FigureTable key={table.id} table={table} />
          ))}

          {section.id === "developments" ? (
            <DevelopmentsTimeline events={section.events} />
          ) : null}

          {section.id === "risks" ? <RiskList risks={section.risks} /> : null}

          <SectionCharts sectionId={section.id} charts={charts} />
        </>
      )}
    </section>
  );
}
