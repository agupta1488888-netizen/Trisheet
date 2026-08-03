import * as React from "react"

import { cn } from "@/lib/utils"

/**
 * The report view's free-text input, for the chat composer.
 *
 * The input screen's rounded-lg/2xl "premium SaaS" radius does not belong
 * here — this follows the report view's own rules instead: a hairline
 * border in --rule and a near-square corner, the same treatment
 * `SourceMarker` already uses (`rounded-xs`). No shadow, no focus glow
 * beyond the shared focus ring. See CLAUDE.md's design system section.
 */
function Textarea({ className, ...props }: React.ComponentProps<"textarea">) {
  return (
    <textarea
      data-slot="textarea"
      className={cn(
        "min-h-16 w-full resize-none rounded-xs border border-input bg-transparent px-2.5 py-2 text-sm text-ink transition-colors outline-none placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-50 aria-invalid:border-destructive aria-invalid:ring-3 aria-invalid:ring-destructive/20 md:text-sm dark:aria-invalid:border-destructive/50 dark:aria-invalid:ring-destructive/40",
        className
      )}
      {...props}
    />
  )
}

export { Textarea }
