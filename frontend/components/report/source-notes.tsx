/**
 * Statements read off links the reader attached to the request.
 *
 * Rendered apart from every other section, and after them, because that is
 * what they are: not part of the filed record, appended to it. The heading
 * says "supplied", the note under it says "not verified", and each statement
 * cites the page it came from with the moment it was read — a web page is not
 * immutable the way a filing is, so a citation to one without a timestamp is
 * not a citation.
 *
 * These can never be figures. The backend produces `SourceNote`, a shape the
 * fact store cannot accept, so nothing here can reach the financial
 * highlights table — see `m13_sources`. That is why this file has no
 * `SourceMarker`, no monospace column and no tabular-nums: there is no figure
 * to right-align, and borrowing the vocabulary of the certified sections
 * would imply a provenance these do not have.
 *
 * Renders nothing at all when no link was supplied. An empty heading is worse
 * than no heading — it invites the reader to wonder what is missing.
 */

import { formatFilingDate } from "@/lib/format";
import {
  FOUND_NOTES_HEADING,
  FOUND_NOTES_NOTE,
  SOURCE_NOTES_HEADING,
  SOURCE_NOTES_NOTE,
} from "@/lib/constants";
import type { SourceNote } from "@/lib/types";

/** The page's own host, which is what a reader actually recognises. */
function hostOf(url: string): string {
  try {
    return new URL(url).host;
  } catch {
    // A URL the backend accepted but the browser will not parse. Showing the
    // raw string is better than dropping a citation.
    return url;
  }
}

function NoteBlock({
  id,
  heading,
  note,
  notes,
}: {
  id: string;
  heading: string;
  note: string;
  notes: readonly SourceNote[];
}) {
  if (notes.length === 0) {
    return null;
  }

  return (
    <section aria-labelledby={id} className="mt-12">
      <h2 id={id} className="font-display text-xl font-semibold text-ink">
        {heading}
      </h2>

      <p className="mt-2 max-w-prose text-sm leading-relaxed text-muted-foreground">
        {note}
      </p>

      <ul className="mt-5 border-t border-rule">
        {notes.map((entry) => (
          <li
            key={`${entry.sourceUrl}-${entry.text}`}
            className="border-b border-rule py-3"
          >
            <p className="text-sm leading-relaxed text-ink">{entry.text}</p>
            <p className="ref mt-1.5 text-xs text-muted-foreground">
              <a
                href={entry.sourceUrl}
                target="_blank"
                rel="noreferrer noopener"
                className="underline underline-offset-4 hover:text-ink"
              >
                {hostOf(entry.sourceUrl)}
              </a>
              <span aria-hidden="true"> · </span>
              Read {formatFilingDate(entry.fetchedAt)}
            </p>
          </li>
        ))}
      </ul>
    </section>
  );
}

export function SourceNotes({ notes }: { notes: readonly SourceNote[] }) {
  // Split by who went looking. A note the reader attached and a note the
  // system found by search are different claims, and one heading covering
  // both would have to be false about one of them.
  const supplied = notes.filter((note) => note.isUserSupplied);
  const found = notes.filter((note) => !note.isUserSupplied);

  return (
    <>
      <NoteBlock
        id="source-notes-heading"
        heading={SOURCE_NOTES_HEADING}
        note={SOURCE_NOTES_NOTE}
        notes={supplied}
      />
      <NoteBlock
        id="found-notes-heading"
        heading={FOUND_NOTES_HEADING}
        note={FOUND_NOTES_NOTE}
        notes={found}
      />
    </>
  );
}
