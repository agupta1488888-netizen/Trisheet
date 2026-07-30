import { fetchReport, fetchReportDocument } from "@/lib/api";
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
 */
export const dynamic = "force-dynamic";

export default async function ReportPage({
  params,
}: {
  params: Promise<{ id: string }>;
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

  return <ReportView document={document.data} />;
}
