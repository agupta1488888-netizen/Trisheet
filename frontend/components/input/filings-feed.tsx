/**
 * The live filing feed, rendered.
 *
 * Presentational and pure: it takes a page and draws it. The fetching lives in
 * `filings-feed-section.tsx`, which is what lets the preview harness mount
 * this against a fixture without a backend, the same way `InputScreen` takes
 * its data functions as props.
 *
 * See `filings-feed-section.tsx` for why nothing here is generated prose.
 */

import {
  FEED_EMPTY_BODY,
  FEED_EMPTY_HEADING,
  FEED_EYEBROW,
  FEED_GUIDANCE_LABEL,
  FEED_HEADING,
  FEED_INTRO,
  FEED_QUOTED_LABEL,
} from "@/lib/constants";
import {
  formatAccession,
  formatEasternTimestamp,
} from "@/lib/format";
import type { FeedItem, FeedPage } from "@/lib/types";

function Quotation({ text, guidance }: { text: string; guidance?: boolean }) {
  return (
    <li className="flex gap-2.5">
      <span
        aria-hidden="true"
        className="mt-2 h-px w-3 shrink-0 bg-white/20"
      />
      <p className="text-[13px] leading-[1.65] text-white/55">
        {guidance ? (
          <span className="ref mr-1.5 text-[11px] tracking-wide text-amber-200/70 uppercase">
            {FEED_GUIDANCE_LABEL}
          </span>
        ) : null}
        &ldquo;{text}&rdquo;
      </p>
    </li>
  );
}

function FilingRow({ item }: { item: FeedItem }) {
  const hasQuotations =
    item.resultSentences.length > 0 || item.guidanceSentences.length > 0;

  return (
    <li className="border-t border-white/[0.07] first:border-t-0">
      <a
        href={item.sourceUrl}
        target="_blank"
        rel="noreferrer"
        className="group flex flex-col gap-3 px-5 py-5 transition-colors hover:bg-white/[0.025] focus-visible:bg-white/[0.04] focus-visible:outline-none sm:flex-row sm:gap-6 sm:px-7 sm:py-6"
      >
        {/* Identity. Fixed width on desktop so the headlines align into a
            column a reader can scan, rather than starting wherever the
            longest company name happens to end. */}
        <div className="flex items-baseline gap-2.5 sm:w-40 sm:shrink-0 sm:flex-col sm:items-start sm:gap-1">
          <span className="ref text-[13px] font-medium text-white">
            {item.ticker ?? item.cik}
          </span>
          <span className="truncate text-[12px] text-white/35 sm:max-w-full">
            {item.companyName}
          </span>
        </div>

        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1.5">
            <span className="ref rounded border border-white/12 bg-white/[0.05] px-1.5 py-0.5 text-[11px] text-white/70">
              {item.form}
            </span>
            <h3 className="font-sans text-[14.5px] leading-snug font-medium text-white/90">
              {item.headline}
            </h3>
          </div>

          {hasQuotations ? (
            <div className="mt-3.5">
              <p className="ref text-[10.5px] tracking-[0.14em] text-white/25 uppercase">
                {FEED_QUOTED_LABEL}
              </p>
              <ul className="mt-2 space-y-2">
                {item.resultSentences.map((sentence) => (
                  <Quotation key={sentence} text={sentence} />
                ))}
                {item.guidanceSentences.map((sentence) => (
                  <Quotation key={sentence} text={sentence} guidance />
                ))}
              </ul>
            </div>
          ) : null}
        </div>

        {/* Provenance. The accession number is the product's identity, so it
            is on the row itself rather than hidden behind the link. */}
        <div className="flex items-center gap-3 sm:w-44 sm:shrink-0 sm:flex-col sm:items-end sm:gap-1">
          <time
            dateTime={item.filedAt}
            className="ref text-[12px] whitespace-nowrap text-white/45"
          >
            {formatEasternTimestamp(item.filedAt)}
          </time>
          <span className="ref text-[11px] whitespace-nowrap text-white/25 transition-colors group-hover:text-white/45">
            {formatAccession(item.accessionNo)}
          </span>
        </div>
      </a>
    </li>
  );
}

export function FilingsFeed({ page }: { page: FeedPage }) {
  return (
    <section
      aria-labelledby="feed-heading"
      className="relative isolate overflow-hidden bg-[#08080a] py-24 sm:py-32"
    >
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-0 bg-[radial-gradient(100%_60%_at_50%_0%,rgba(255,255,255,0.035),transparent_65%)]"
      />
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-white/[0.09] to-transparent"
      />

      <div className="relative mx-auto max-w-4xl px-5 sm:px-8">
        <p className="ref text-[11px] tracking-[0.18em] text-white/40 uppercase">
          {FEED_EYEBROW}
        </p>
        <h2
          id="feed-heading"
          className="mt-4 font-sans text-3xl font-semibold tracking-[-0.03em] text-white sm:text-4xl"
        >
          {FEED_HEADING}
        </h2>
        <p className="mt-4 max-w-2xl text-[15px] leading-[1.7] text-white/45">
          {FEED_INTRO}
        </p>

        {/* Two timestamps rather than one. EDGAR publishes nothing overnight
            or at a weekend, so a three-day-old newest filing is usually a
            working feed — and the only way to tell that from a poller that
            died on Friday is to say when EDGAR last had something to say and
            when this deployment last asked. */}
        {page.lastCheckedAt !== null || page.latestFiledAt !== null ? (
          <dl className="mt-7 flex flex-wrap items-center gap-x-6 gap-y-2 text-[12px]">
            {page.lastCheckedAt !== null ? (
              <div className="flex items-center gap-2">
                <span
                  aria-hidden="true"
                  className="size-1.5 rounded-full bg-emerald-400/70"
                />
                <dt className="text-white/30">Last checked</dt>
                <dd className="ref text-white/55">
                  {formatEasternTimestamp(page.lastCheckedAt)}
                </dd>
              </div>
            ) : null}
            {page.latestFiledAt !== null ? (
              <div className="flex items-center gap-2">
                <dt className="text-white/30">Newest filing</dt>
                <dd className="ref text-white/55">
                  {formatEasternTimestamp(page.latestFiledAt)}
                </dd>
              </div>
            ) : null}
          </dl>
        ) : null}

        <div className="mt-9 overflow-hidden rounded-2xl border border-white/[0.09] bg-white/[0.018]">
          {page.items.length === 0 ? (
            <div className="px-7 py-14 text-center">
              <p className="font-sans text-[15px] font-medium text-white/70">
                {FEED_EMPTY_HEADING}
              </p>
              <p className="mx-auto mt-2 max-w-md text-[13px] leading-relaxed text-white/35">
                {FEED_EMPTY_BODY}
              </p>
            </div>
          ) : (
            <ul>
              {page.items.map((item) => (
                <FilingRow key={item.accessionNo} item={item} />
              ))}
            </ul>
          )}
        </div>
      </div>
    </section>
  );
}
