/**
 * The site header.
 *
 * One component, two token sets, so the wordmark is guaranteed identical on
 * both surfaces rather than drifting apart as two separate implementations.
 * `variant="dark"` renders over the input screen's hero; `variant="paper"`
 * sits above the report masthead, hairline border only — never a shadow or
 * gradient, per the report view's design rules.
 */

import Link from "next/link";
import { MessageCircle } from "lucide-react";

import { cn } from "@/lib/utils";

export interface SiteHeaderProps {
  variant: "dark" | "paper";
  /** Report view only: a way back to search, rendered beside the wordmark. */
  backHref?: string;
  /**
   * The "Ask" control. An `href` scrolls to the assistant showcase (input
   * screen, where there is no live report to chat about yet); `onAskClick`
   * opens the report view's own assistant panel directly.
   */
  askHref?: string;
  onAskClick?: () => void;
}

export function SiteHeader({
  variant,
  backHref,
  askHref,
  onAskClick,
}: SiteHeaderProps) {
  const isDark = variant === "dark";

  return (
    <header
      className={cn(
        "relative z-20 mx-auto flex max-w-[1400px] items-center justify-between gap-6 px-6 sm:px-10",
        isDark ? "pt-8" : "border-b border-rule py-5",
      )}
    >
      <div className="flex items-center gap-6">
        <Link href="/" className="flex items-center gap-2.5">
          <span
            aria-hidden="true"
            className={cn(
              "flex size-8 items-center justify-center rounded-lg border text-[13px] font-semibold",
              isDark
                ? "border-white/12 bg-white/[0.06] text-white"
                : "border-rule bg-wash text-ink",
            )}
          >
            T
          </span>
          <span
            className={cn(
              "text-[15px] font-medium tracking-[-0.01em]",
              isDark ? "text-white" : "text-ink",
            )}
          >
            Trisheet
          </span>
        </Link>

        {backHref === undefined ? null : (
          <Link
            href={backHref}
            className="text-sm text-certified underline underline-offset-4 hover:text-ink"
          >
            ← Back to search
          </Link>
        )}
      </div>

      {(() => {
        const askClassName = cn(
          "flex items-center gap-1.5 text-sm font-medium",
          isDark
            ? "text-white/70 hover:text-white"
            : "text-ink hover:text-certified",
        );
        const icon = (
          <MessageCircle
            aria-hidden="true"
            strokeWidth={2}
            className="size-4"
          />
        );

        if (onAskClick !== undefined) {
          return (
            <button type="button" onClick={onAskClick} className={askClassName}>
              {icon}
              Ask
            </button>
          );
        }

        if (askHref !== undefined) {
          return (
            <a href={askHref} className={askClassName}>
              {icon}
              Ask
            </a>
          );
        }

        return null;
      })()}
    </header>
  );
}
