import type { Filing } from "@/lib/types";

/**
 * The provenance rail — the product's signature element.
 *
 * A persistent column beside the report in which each figure's source appears
 * as a reference card: form type, accession number, filing date, link.
 * It stays visible. It is never collapsed into a footer.
 *
 * Presentational only: it renders what it is given and fetches nothing.
 * Hover linking between figures and sources is wired in phase 1.
 */
export function ProvenanceRail({ sources }: { sources: readonly Filing[] }) {
  return (
    <aside
      aria-label="Sources"
      className="lg:sticky lg:top-16 lg:self-start"
      data-provenance-rail
    >
      <h2 className="text-sm font-medium tracking-wide text-muted-foreground">
        Sources
      </h2>

      {sources.length === 0 ? (
        <p className="mt-4 border-t border-rule pt-4 text-sm text-muted-foreground">
          Not disclosed
        </p>
      ) : (
        <ul className="mt-4 border-t border-rule">
          {sources.map((source) => (
            <li
              key={source.accessionNo}
              className="border-b border-rule py-3"
              data-accession={source.accessionNo}
            >
              <div className="flex items-baseline justify-between gap-3">
                <span className="ref text-sm text-certified">
                  {source.form}
                </span>
                <span className="figure text-xs text-muted-foreground">
                  {source.filedDate}
                </span>
              </div>
              <a
                href={source.primaryDocUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="ref mt-1 block text-xs text-muted-foreground underline-offset-2 hover:text-ink hover:underline"
              >
                {source.accessionNo}
              </a>
            </li>
          ))}
        </ul>
      )}
    </aside>
  );
}
