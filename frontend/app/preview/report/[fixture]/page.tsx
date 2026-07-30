import { notFound } from "next/navigation";

import { FIXTURES, findFixture } from "@/lib/mock";
import { PreviewNotice } from "@/components/preview/preview-notice";
import { ReportView } from "@/components/report/report-view";

/** The report view, mounted against a fixture document. */

export function generateStaticParams() {
  return FIXTURES.map((fixture) => ({ fixture: fixture.slug }));
}

export default async function PreviewReport({
  params,
}: {
  params: Promise<{ fixture: string }>;
}) {
  const { fixture: slug } = await params;
  const fixture = findFixture(slug);

  if (fixture === undefined) {
    notFound();
  }

  return (
    <>
      <PreviewNotice what={fixture.title + "."} />
      <ReportView document={fixture.document} />
    </>
  );
}
