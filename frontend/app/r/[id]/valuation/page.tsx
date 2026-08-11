import {
  fetchReport,
  fetchReportDocument,
  fetchValuation,
} from "@/lib/api";
import { fromQuery, toQuery } from "@/lib/valuation-url";
import { ProgressScreen } from "@/components/progress/progress-screen";
import { ReportUnavailable } from "@/components/report/report-unavailable";
import { ValuationPageView } from "@/components/report/valuation-page-view";

/**
 * The valuation route.
 *
 * Same three outcomes as the report route, because a reader can land here
 * directly (a shared link, a bookmark) without having passed through the
 * report page first: still running, unreachable, or complete.
 *
 * Assumptions live in the query string here too, and for the same reason —
 * a shared valuation link should paint with the scenario it was shared to
 * carry, not the defaults.
 */
export const dynamic = "force-dynamic";

export default async function ValuationPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { id } = await params;

  const report = await fetchReport(id);
  if (!report.ok) {
    return <ReportUnavailable error={report.error} />;
  }

  if (report.data.status !== "complete") {
    return (
      <ProgressScreen
        reportId={id}
        ticker={report.data.ticker}
        initialStatus={report.data.status}
        initialErrorMessage={report.data.errorMessage}
      />
    );
  }

  const document = await fetchReportDocument(id);
  if (!document.ok) {
    return <ReportUnavailable error={document.error} />;
  }

  const assumptions = fromQuery(await searchParams);
  const valuation = await fetchValuation(id, toQuery(assumptions));

  return (
    <ValuationPageView
      document={document.data}
      valuation={valuation.ok ? valuation.data : null}
      assumptions={assumptions}
    />
  );
}
