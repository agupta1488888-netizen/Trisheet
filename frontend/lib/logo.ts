/**
 * Company logo URLs, by ticker.
 *
 * logo.dev maps ticker symbols to domains internally, so this works for an
 * arbitrary US ticker without us ever knowing the company's domain (EDGAR
 * submissions carry no website field). The token is a publishable key, safe
 * to ship to the browser. Returns null when unconfigured so the caller can
 * fall back — a logo is decoration, never a hard dependency.
 */

/**
 * The publishable key, as a default rather than a requirement.
 *
 * This is not a secret and is not treated as one by the service that issues
 * it: it is labelled "safe to share publicly", it is only accepted by the
 * image endpoint, and it is visible in the browser bundle of every site that
 * uses it — committing it changes nothing about who can read it. What it does
 * change is that the logo works from a clean checkout and from any deployment,
 * rather than silently disappearing whenever a `NEXT_PUBLIC_` variable is
 * missing at build time, which is a failure with no symptom other than the
 * logo not being there.
 *
 * The environment still wins where it is set, so a deployment can point at a
 * different key without a code change. Quota is protected on logo.dev's side
 * by restricting the key to allowed domains, not by hiding it.
 */
const DEFAULT_LOGO_DEV_TOKEN = "pk_EpUmEuAmQ4CmM7w_F2i6gw";

const LOGO_DEV_TOKEN =
  process.env.NEXT_PUBLIC_LOGO_DEV_TOKEN || DEFAULT_LOGO_DEV_TOKEN;

export function companyLogoUrl(ticker: string, size = 96): string | null {
  if (!LOGO_DEV_TOKEN) return null;

  const params = new URLSearchParams({
    token: LOGO_DEV_TOKEN,
    size: String(size),
    format: "png",
    fallback: "404",
  });

  return `https://img.logo.dev/ticker/${encodeURIComponent(ticker)}?${params.toString()}`;
}
