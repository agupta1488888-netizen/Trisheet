/**
 * The standing notice on every preview route.
 *
 * A screenshot of a preview page must never be mistakable for a generated
 * report. The figures behind these routes are fixtures — plausible in shape so
 * the layout is worth testing, and invented, so they say so at the top of the
 * page in the colour this product reserves for things that need flagging.
 */

import Link from "next/link";

export function PreviewNotice({ what }: { what: string }) {
  return (
    <div className="border-b-2 border-b-flag bg-paper">
      <div className="mx-auto flex max-w-7xl flex-wrap items-baseline gap-x-4 gap-y-1 px-5 py-2 sm:px-8">
        <span className="ref text-xs font-medium text-flag">
          Fixture preview
        </span>
        <span className="min-w-0 flex-1 text-xs text-muted-foreground">
          {what} Figures on this page are placeholders for interface
          development. They are not sourced from filings and this is not a
          generated report.
        </span>
        <Link
          href="/preview"
          className="text-xs text-certified underline underline-offset-4 hover:text-ink"
        >
          All fixtures
        </Link>
      </div>
    </div>
  );
}
