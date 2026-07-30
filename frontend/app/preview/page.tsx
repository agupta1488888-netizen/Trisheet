import Link from "next/link";

import { FIXTURES } from "@/lib/mock";
import { PreviewNotice } from "@/components/preview/preview-notice";

/**
 * The fixture index.
 *
 * Every screen in the product, mounted against typed fixtures so it can be
 * checked before the pipeline exists. These routes import `lib/mock`; nothing
 * else in the application does.
 */

interface PreviewRoute {
  href: string;
  title: string;
  purpose: string;
}

const SCREEN_ROUTES: readonly PreviewRoute[] = [
  {
    href: "/preview/input",
    title: "Input screen",
    purpose:
      "Autocomplete, depth selector, example chips. Type “berkshire” for the disambiguation surface and “zzzz” for the not-found surface.",
  },
  {
    href: "/preview/progress/nominal",
    title: "Progress feed · nominal run",
    purpose:
      "Twelve steps settling in order, each reporting the count it produced.",
  },
  {
    href: "/preview/progress/degraded",
    title: "Progress feed · degraded run",
    purpose:
      "Market data unreachable. Steps are skipped rather than failed and the run still completes.",
  },
  {
    href: "/preview/progress/failed",
    title: "Progress feed · stopped run",
    purpose:
      "A run that cannot continue, stating what happened and what to do next.",
  },
];

function RouteList({
  heading,
  routes,
}: {
  heading: string;
  routes: readonly PreviewRoute[];
}) {
  return (
    <section className="mt-10">
      <h2 className="text-lg text-ink">{heading}</h2>
      <ul className="mt-3 border-t border-rule">
        {routes.map((route) => (
          <li key={route.href} className="border-b border-rule">
            <Link
              href={route.href}
              className="block py-3 hover:bg-wash focus-visible:bg-wash"
            >
              <span className="text-sm font-medium text-ink">
                {route.title}
              </span>
              <span className="mt-1 block max-w-prose text-xs leading-relaxed text-muted-foreground">
                {route.purpose}
              </span>
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}

export default function PreviewIndex() {
  return (
    <>
      <PreviewNotice what="This is the interface development harness." />
      <main id="main" className="mx-auto max-w-3xl px-5 py-14 sm:px-8">
        <h1 className="text-3xl text-ink">Fixtures</h1>
        <p className="mt-2 max-w-prose text-sm leading-relaxed text-muted-foreground">
          Each route below mounts a real product screen against typed fixture
          data. The components are the ones that ship; only the source of the
          data differs.
        </p>

        <RouteList heading="Screens" routes={SCREEN_ROUTES} />

        <RouteList
          heading="Reports"
          routes={FIXTURES.map((fixture) => ({
            href: `/preview/report/${fixture.slug}`,
            title: fixture.title,
            purpose: fixture.purpose,
          }))}
        />
      </main>
    </>
  );
}
