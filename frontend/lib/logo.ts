/**
 * Company logo URLs, by ticker.
 *
 * logo.dev maps ticker symbols to domains internally, so this works for an
 * arbitrary US ticker without us ever knowing the company's domain (EDGAR
 * submissions carry no website field). The token is a publishable key, safe
 * to ship to the browser. Returns null when unconfigured so the caller can
 * fall back — a logo is decoration, never a hard dependency.
 */

const LOGO_DEV_TOKEN = process.env.NEXT_PUBLIC_LOGO_DEV_TOKEN;

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
