import {
  fetchReport,
  fetchReportDocument,
  fetchValuation,
} from "@/lib/api";
import { fromQuery, toQuery } from "@/lib/valuation-url";
import { ProgressScreen } from "@/components/progress/progress-screen";
import { ReportUnavailable } from "@/components/report/report-unavailable";
import { ReportView } from "@/components/report/report-view";

/**
 * The report route.
 *
 * One id, three outcomes: the run is still going and the reader watches it;
 * the run finished and the document renders; or the report cannot be reached
 * and the page says why. There is no fourth branch that renders a blank screen
 * or a stack trace.
 *
 * Status is fetched on the server so a finished report paints without a client
 * round trip. The progress screen refreshes this route once its run settles,
 * which is what promotes the first outcome into the second.
 *
 * The workbench's assumptions arrive in the query string, and its first
 * response is fetched here rather than in the browser. That is what lets a
 * shared link paint with the scenario it was shared to carry, instead of
 * showing the defaults and correcting itself a moment later.
 */
export const dynamic = "force-dynamic";

export default async function ReportPage({
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

  // A valuation is an enrichment, never a precondition. A filer whose figures
  // do not support one still gets its whole report; the section says why.
  const assumptions = fromQuery(await searchParams);
  const valuation = await fetchValuation(id, toQuery(assumptions));

  return (
    <ReportView
      document={document.data}
      valuation={valuation.ok ? valuation.data : null}
      assumptions={assumptions}
    />
  );
}
