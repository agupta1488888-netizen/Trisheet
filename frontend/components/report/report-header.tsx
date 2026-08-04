/**
 * The report masthead.
 *
 * Identity first: who the filer is, which annual form they actually file, and
 * what currency they report in. A reader who does not know whether they are
 * looking at a 10-K or a 20-F cannot read the rest of the page correctly, so
 * that fact is stated before any figure appears.
 */

import {
  formatCount,
  formatFilingDate,
  formatFiscalYearEnd,
} from "@/lib/format";
import type { AnalysisDepth, Company } from "@/lib/types";
import { DEPTH_OPTIONS } from "@/lib/constants";

/** The annual form each filer type files. Detected upstream, never assumed. */
const ANNUAL_FORM: Readonly<Record<Company["filerType"], string>> = {
  domestic: "10-K",
  foreign: "20-F",
  canadian: "40-F",
};

const FILER_DESCRIPTION: Readonly<Record<Company["filerType"], string>> = {
  domestic: "Domestic filer",
  foreign: "Foreign private issuer",
  canadian: "Canadian filer, MJDS",
};

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <dt className="text-[0.68rem] text-muted-foreground">{label}</dt>
      <dd className="ref text-sm text-ink">{value}</dd>
    </div>
  );
}

export function ReportHeader({
  company,
  depth,
  completedAt,
}: {
  company: Company;
  depth: AnalysisDepth;
  completedAt: string | null;
}) {
  const depthLabel =
    DEPTH_OPTIONS.find((option) => option.value === depth)?.label ?? depth;

  return (
    <header>
      <p className="ref text-sm text-certified">{company.ticker}</p>
      <h1 className="mt-1 text-4xl leading-tight text-ink">{company.name}</h1>

      <dl className="mt-6 grid grid-cols-2 gap-x-8 gap-y-4 border-t border-rule pt-4 sm:grid-cols-3 lg:grid-cols-5">
        <Detail label="Annual form" value={ANNUAL_FORM[company.filerType]} />
        <Detail label="Filer" value={FILER_DESCRIPTION[company.filerType]} />
        <Detail label="CIK" value={company.cik} />
        <Detail
          label="Reporting currency"
          value={company.reportingCurrency ?? "Not disclosed"}
        />
        <Detail label="Sector" value={company.sector ?? "Not disclosed"} />

        {/* Identity, off the filer's own submissions document. Every one of
            these renders "Not disclosed" rather than vanishing when EDGAR
            does not state it — a reader should be able to tell the
            difference between a fact we do not have and a field we did not
            look for. Employees is usually the absent one: most filers state
            headcount in prose and never tag it. */}
        <Detail label="Exchange" value={company.exchange ?? "Not disclosed"} />
        <Detail
          label="Headquarters"
          value={company.headquarters ?? "Not disclosed"}
        />
        <Detail
          label="Incorporated"
          value={company.stateOfIncorporation ?? "Not disclosed"}
        />
        <Detail
          label="Employees"
          value={
            company.employees === null
              ? "Not disclosed"
              : formatCount(company.employees)
          }
        />
        <Detail
          label="Fiscal year end"
          value={formatFiscalYearEnd(company.fiscalYearEnd)}
        />
      </dl>

      <p className="mt-4 text-xs text-muted-foreground">
        {depthLabel} analysis
        {completedAt === null
          ? ""
          : ` · generated ${formatFilingDate(completedAt)}`}
        {company.sicCode === null ? "" : ` · SIC ${company.sicCode}`}
      </p>
    </header>
  );
}
