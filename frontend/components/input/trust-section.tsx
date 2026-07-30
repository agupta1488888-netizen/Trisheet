/**
 * Three claims a reader can verify, not three reasons to trust blindly.
 *
 * Each names the control that makes it true rather than asserting the
 * outcome alone — "checked before it ships" without saying what checks is
 * exactly the kind of unsourced claim this product refuses to make about a
 * company, so it should not make one about itself either.
 */

import { FileCheck2, Link2, ShieldCheck } from "lucide-react";

const POINTS = [
  {
    icon: Link2,
    title: "Sourced from filings",
    body: "Every figure links to the filing and accession number it was read from. Point at it to see the source.",
  },
  {
    icon: ShieldCheck,
    title: "Checked before it ships",
    body: "A blocking gate re-reads every written sentence against the underlying facts. A mismatch withholds the report — it does not add a caveat.",
  },
  {
    icon: FileCheck2,
    title: "Nothing invented",
    body: "The model is only ever shown numbers already extracted from a filing. It writes about them; it never calculates or recalls one.",
  },
] as const;

export function TrustSection() {
  return (
    <section
      aria-label="How this is different"
      className="grid grid-cols-1 gap-x-8 gap-y-8 border-t border-rule pt-10 sm:grid-cols-3"
    >
      {POINTS.map(({ icon: Icon, title, body }) => (
        <div key={title} className="flex flex-col gap-3">
          <Icon aria-hidden="true" strokeWidth={1.5} className="size-5 text-certified" />
          <h2 className="text-base text-ink">{title}</h2>
          <p className="text-sm leading-relaxed text-muted-foreground">
            {body}
          </p>
        </div>
      ))}
    </section>
  );
}
