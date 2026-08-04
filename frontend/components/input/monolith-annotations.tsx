"use client";

/**
 * The annotation cards floating beside the monolith.
 *
 * Every figure on them comes from the fixture set, not from imagination. The
 * reference mockup these follow carried invented values — an accession number
 * belonging to no filing, a shortened `us-gaap:Revenues` tag, a filing date
 * that does not match its period. CLAUDE.md is explicit that an illustrative
 * figure must be labelled as a sample and drawn from an existing fixture, so
 * these are transcribed from the domestic fixture and the cluster carries a
 * "Sample" marker. See `SAMPLE` below for why they are copied rather than
 * imported.
 *
 * The card titled "Summary" is deliberately not titled "AI Summary". CLAUDE.md
 * forbids the word "AI" in the interface, and a grep confirms it appears in no
 * user-visible string anywhere in the app today. The rule holds; this does not
 * break it.
 *
 * Positions are static rather than projected from the 3D anchor points. The
 * object floats, turns and parallaxes, so a projected endpoint would drift
 * around under the card; the reference is a still composition and fixed
 * endpoints match it. The anchor dots in the scene are placed to meet these.
 */

/**
 * Copied from the domestic fixture rather than imported from it.
 *
 * `lib/mock` is only ever imported by routes under `/preview` — that boundary
 * is what keeps fixture data out of the production bundle, and this component
 * renders on `/`. Importing the module here pulled the whole fixture set into
 * the homepage's server bundle and broke the prerender.
 *
 * Every value below is transcribed from `lib/mock/domestic.ts` (Apple Inc.,
 * CIK 0000320193, FY2024 10-K, metric `income.revenue`). If that fixture
 * changes, change these too — they are illustrative, and the cluster says so.
 */
const SAMPLE = {
  form: "10-K · FY2024",
  accessionNo: "0000320193-24-000123",
  filedDate: "2024-11-01",
  tag: "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
  value: "391,035 USD millions",
  period: "FY2024",
} as const;

interface CardField {
  readonly label: string;
  readonly value: string;
  /** Monospace, for tags and accession numbers. */
  readonly mono?: boolean;
}

function Card({
  title,
  glyph,
  fields,
  body,
  className,
}: {
  title: string;
  glyph: string;
  fields?: readonly CardField[];
  body?: string;
  className: string;
}) {
  return (
    <div
      className={`pointer-events-none absolute w-[11.75rem] rounded-xl border border-white/10 bg-[#0d0d10]/90 px-4 py-3.5 shadow-2xl shadow-black/70 backdrop-blur-xl ${className}`}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="text-[13px] font-medium text-white">{title}</span>
        <span aria-hidden="true" className="text-[11px] text-white/35">
          {glyph}
        </span>
      </div>

      {fields === undefined
        ? null
        : fields.map((field) => (
            <div key={field.label} className="mt-2.5">
              <div className="text-[10px] tracking-[0.04em] text-white/30">
                {field.label}
              </div>
              <div
                className={`mt-0.5 text-[11px] leading-snug break-words text-white/70 ${
                  field.mono === true ? "ref" : ""
                }`}
              >
                {field.value}
              </div>
            </div>
          ))}

      {body === undefined ? null : (
        <p className="mt-2.5 text-[11px] leading-[1.55] text-white/60">{body}</p>
      )}
    </div>
  );
}

/** A hairline from a card toward the object, matching the reference's leaders. */
function Leader({ className }: { className: string }) {
  return (
    <span
      aria-hidden="true"
      className={`pointer-events-none absolute bg-white/15 ${className}`}
    />
  );
}

export function MonolithAnnotations() {
  return (
    // Hidden below xl: under that width the column cannot hold cards without
    // running into the headline.
    <div aria-hidden="true" className="pointer-events-none absolute inset-0 hidden xl:block">
      <Card
        title="SEC Filing"
        glyph="↗"
        className="top-[4%] right-0"
        fields={[
          { label: "Form", value: SAMPLE.form },
          { label: "Accession No.", value: SAMPLE.accessionNo, mono: true },
          { label: "Filed on", value: SAMPLE.filedDate, mono: true },
        ]}
      />
      <Leader className="top-[11%] right-[11.75rem] h-px w-8" />

      <Card
        title="XBRL Tag"
        glyph="< >"
        className="top-[38%] left-[-5.5rem]"
        fields={[
          { label: "Revenue", value: SAMPLE.tag, mono: true },
          { label: "Value", value: SAMPLE.value, mono: true },
          { label: "Period", value: SAMPLE.period },
        ]}
      />
      <Leader className="top-[45%] left-[6.25rem] h-px w-10" />

      <Card
        title="Summary"
        glyph="✦"
        className="bottom-[10%] right-0"
        body="Revenue rose across the period, with services outpacing products. Every figure behind this sentence links to the filing it came from."
      />
      <Leader className="bottom-[18%] right-[11.75rem] h-px w-8" />

      <span className="absolute bottom-[2%] right-0 text-[10px] tracking-[0.08em] text-white/25 uppercase">
        Sample · fixture data
      </span>
    </div>
  );
}
