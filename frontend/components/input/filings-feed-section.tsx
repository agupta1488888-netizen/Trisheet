/**
 * The live filing feed.
 *
 * Sits below the hero, in the same "premium SaaS" exception zone CLAUDE.md
 * carves out for `components/input/` — so the dark cinematic backdrop and the
 * rounded cards are permitted here in a way they are not under
 * `components/report/`.
 *
 * What does not lapse in the exception zone: tickers, accession numbers and
 * dates render in the data face with tabular figures, guidance is labelled and
 * never mixed into results, and every item links to the filing it came from.
 * Those are the product's identity rather than the report view's styling, and
 * a landing page is not a reason to suspend them.
 *
 * Nothing here is generated. A headline is EDGAR's own item label; a quoted
 * sentence is the company's own words from the press release attached to the
 * filing. There is no summary field on `FeedItem` and deliberately so — a
 * written sentence on this page would be the one unsourced claim in a product
 * whose entire proposition is that there are none.
 *
 * A server component: the feed is the same for every visitor and is worth
 * having in the server-rendered payload, and a client-side fetch would leave
 * the section empty until it landed.
 */

import { FEED_ITEMS_SHOWN, FEED_REVALIDATE_SECONDS } from "@/lib/constants";
import { fetchFeed } from "@/lib/api";
import type { FeedPage } from "@/lib/types";
import { FilingsFeed } from "@/components/input/filings-feed";

export async function FilingsFeedSection() {
  const page: FeedPage = await fetchFeed(
    FEED_ITEMS_SHOWN,
    FEED_REVALIDATE_SECONDS,
  );
  return <FilingsFeed page={page} />;
}
