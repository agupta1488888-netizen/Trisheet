"use client";

import { useState } from "react";

import { companyLogoUrl } from "@/lib/logo";

/**
 * Decorative only — the ticker and company name are already rendered as
 * adjacent text in the masthead, so the image carries no information a
 * screen reader needs to announce.
 */
export function CompanyLogo({ ticker }: { ticker: string }) {
  const [failed, setFailed] = useState(false);
  const src = companyLogoUrl(ticker);

  if (!src || failed) {
    return (
      <div
        aria-hidden="true"
        className="ref flex h-12 w-12 shrink-0 items-center justify-center border border-rule text-sm text-ink"
      >
        {ticker.slice(0, 2).toUpperCase()}
      </div>
    );
  }

  return (
    // eslint-disable-next-line @next/next/no-img-element -- external logo, needs onError fallback
    <img
      src={src}
      alt=""
      aria-hidden="true"
      width={48}
      height={48}
      className="h-12 w-12 shrink-0 border border-rule object-contain"
      onError={() => setFailed(true)}
    />
  );
}
