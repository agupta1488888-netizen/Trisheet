import { ProvenanceRail } from "@/components/report/provenance-rail";

/**
 * Report view. Structural shell only — data loading is wired in phase 1.
 *
 * The layout is fixed here on purpose: the provenance rail is a persistent
 * column beside the report. It is never collapsed into a footer.
 */
export default async function ReportPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;

  return (
    <div className="mx-auto grid max-w-6xl grid-cols-1 gap-10 px-6 py-16 lg:grid-cols-[1fr_18rem]">
      <main>
        <h1 className="text-3xl">Report</h1>
        <p className="ref mt-1 text-sm text-muted-foreground">{id}</p>
        <div className="mt-8 border-t border-rule pt-8">
          <p className="text-muted-foreground">Not disclosed</p>
        </div>
      </main>

      <ProvenanceRail sources={[]} />
    </div>
  );
}
