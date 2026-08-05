/**
 * What the report route renders when there is no report to render.
 *
 * States what happened and what to do, in the interface's voice. Never a stack
 * trace, never a blank screen, never "something went wrong".
 */

import Link from "next/link";
import { AlertTriangle } from "lucide-react";

import type { ApiError } from "@/lib/types";

export function ReportUnavailable({ error }: { error: ApiError }) {
  return (
    <main
      id="main"
      className="mx-auto flex min-h-screen max-w-2xl flex-col justify-center px-5 py-20 sm:px-8"
    >
      <AlertTriangle
        aria-hidden="true"
        strokeWidth={1.5}
        className="size-6 text-muted-foreground"
      />
      <h1 className="mt-4 text-2xl text-ink">{error.message}</h1>

      {error.detail === null ? null : (
        <p className="ref mt-3 text-xs text-muted-foreground">{error.detail}</p>
      )}

      <p className="mt-6 border-t border-rule pt-4">
        <Link
          href="/"
          className="text-sm text-certified underline underline-offset-4 hover:text-ink"
        >
          Start a new profile
        </Link>
      </p>
    </main>
  );
}
