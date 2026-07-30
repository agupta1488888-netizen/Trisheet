/**
 * A sample of what a finished report looks like.
 *
 * The figures are Apple's real FY2024 10-K numbers, the same fixture set the
 * `/preview` routes render from — labelled "Sample report" throughout so it
 * is never mistaken for a live result. Exists so a first-time visitor sees
 * the citation pattern (a marker linking a figure to the filing it came
 * from) before they have generated a report of their own.
 */

import { FileText } from "lucide-react";

const METRICS = [
  { label: "Revenue", value: "391,035", unit: "USD million" },
  { label: "Gross margin", value: "46.2", unit: "percent" },
  { label: "Operating cash flow", value: "118,254", unit: "USD million" },
] as const;

const SENTENCE =
  "Revenue rose to $391.0 billion in FY2024, from $383.3 billion the year before.";

const SOURCE = {
  company: "Apple Inc.",
  ticker: "AAPL",
  form: "10-K",
  filedDate: "1 Nov 2024",
  accessionNo: "0000320193-24-000123",
};

function Marker() {
  return (
    <sup className="ref ml-0.5 text-[0.62rem] text-certified">1</sup>
  );
}

export function ReportPreview() {
  return (
    <figure className="border border-rule bg-wash/50 p-6 sm:p-8">
      <figcaption className="flex flex-wrap items-baseline justify-between gap-2 border-b border-rule pb-3">
        <span className="text-[0.66rem] tracking-wide text-muted-foreground uppercase">
          Sample report
        </span>
        <span className="ref text-[0.7rem] text-muted-foreground">
          {SOURCE.company} · {SOURCE.ticker}
        </span>
      </figcaption>

      <dl className="mt-5 grid grid-cols-1 gap-4 sm:grid-cols-3">
        {METRICS.map((metric) => (
          <div key={metric.label} className="flex flex-col gap-1">
            <dt className="text-[0.64rem] text-muted-foreground">
              {metric.label}
            </dt>
            <dd className="figure text-base text-ink sm:text-lg">
              {metric.value}
              <Marker />
            </dd>
            <dd className="text-[0.6rem] text-muted-foreground">
              {metric.unit}
            </dd>
          </div>
        ))}
      </dl>

      <p className="mt-5 border-t border-rule pt-4 text-sm leading-relaxed text-ink">
        {SENTENCE}
        <Marker />
      </p>

      <div className="mt-5 flex items-start gap-3 border-t border-rule pt-4">
        <FileText
          aria-hidden="true"
          strokeWidth={1.5}
          className="mt-0.5 size-4 shrink-0 text-certified"
        />
        <div className="text-[0.7rem] leading-relaxed text-muted-foreground">
          <p className="ref text-ink">
            1 &nbsp;{SOURCE.form}, filed {SOURCE.filedDate}
          </p>
          <p className="ref">{SOURCE.accessionNo}</p>
        </div>
      </div>
    </figure>
  );
}
