"use client";

/**
 * The download control.
 *
 * The backend renders a PDF and an XLSX for every completed report, stores
 * them, and serves each from `/reports/{id}/artifacts/{kind}`. Until now
 * nothing in the interface offered them, so a reader who wanted the document
 * on paper — which is what a research note is for — had no way to get it.
 *
 * Three states, and each is stated rather than implied:
 *
 *   published    a link, with the file's size, so a reader knows what they
 *                are about to fetch
 *   built but    the file exists and could not be published to storage. The
 *   unpublished  report is still complete, because storage is a sink and not
 *                a dependency, so this says so instead of disappearing
 *   not built    the reason the backend gave, in its own voice
 *
 * A missing artifact never renders as a dead button. A control that looks
 * live and does nothing is worse than an absent one, and on a document whose
 * whole claim is that it does not hide gaps it would be the wrong lie to
 * tell.
 */

import { formatBytes } from "@/lib/format";
import type { ArtifactKind, ArtifactRef } from "@/lib/types";

const KIND_LABEL: Readonly<Record<ArtifactKind, string>> = {
  pdf: "PDF",
  xlsx: "Excel",
};

/** Declared here so the order is the document's, not the backend's array. */
const KIND_ORDER: readonly ArtifactKind[] = ["pdf", "xlsx"];

function Download({ artifact }: { artifact: ArtifactRef }) {
  const label = KIND_LABEL[artifact.kind];

  if (artifact.url === null) {
    const reason =
      artifact.unavailableReason ??
      "This file was rendered but could not be published.";
    return (
      <span className="text-xs text-muted-foreground">
        {label} — {reason}
      </span>
    );
  }

  return (
    <a
      href={artifact.url}
      download
      className="text-xs text-certified underline underline-offset-4 hover:text-ink focus-visible:text-ink"
    >
      Download {label}
      <span className="figure ml-1.5 text-[0.68rem] text-muted-foreground">
        {formatBytes(artifact.sizeBytes)}
      </span>
    </a>
  );
}

export function ArtifactDownloads({
  artifacts,
}: {
  artifacts: readonly ArtifactRef[];
}) {
  if (artifacts.length === 0) {
    return null;
  }

  const ordered = KIND_ORDER.map((kind) =>
    artifacts.find((artifact) => artifact.kind === kind),
  ).filter((artifact) => artifact !== undefined);

  if (ordered.length === 0) {
    return null;
  }

  return (
    <div className="flex flex-wrap items-baseline gap-x-6 gap-y-1 border-t border-rule pt-3">
      <span className="text-[0.68rem] text-muted-foreground">This report</span>
      {ordered.map((artifact) => (
        <Download key={artifact.kind} artifact={artifact} />
      ))}
    </div>
  );
}
