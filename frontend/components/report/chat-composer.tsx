"use client";

/**
 * The message field.
 *
 * Enter submits, Shift+Enter inserts a newline — the same convention as
 * every other chat surface, so it needs no instruction. `disabledReason` is
 * shown in place of the hint once the report isn't ready or the assistant
 * isn't configured; the field disables rather than accepting messages it
 * cannot answer.
 */

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

export function ChatComposer({
  onSubmit,
  isSending,
  disabledReason,
}: {
  onSubmit: (message: string) => void;
  isSending: boolean;
  /** Non-null once the conversation is gated: report not ready, assistant
   * unavailable, or the report could not be found. */
  disabledReason: string | null;
}) {
  const [value, setValue] = useState("");
  const isDisabled = disabledReason !== null;

  const submit = () => {
    const trimmed = value.trim();
    if (trimmed === "" || isSending || isDisabled) {
      return;
    }
    onSubmit(trimmed);
    setValue("");
  };

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
      className="border-t border-rule p-3"
    >
      <p className="mb-2 text-xs text-muted-foreground">
        {disabledReason ?? "Asks only what this report's filed data can answer."}
      </p>

      <div className="flex items-end gap-2">
        <Textarea
          value={value}
          onChange={(event) => {
            setValue(event.target.value);
          }}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              submit();
            }
          }}
          placeholder="Ask about this report…"
          rows={2}
          disabled={isSending || isDisabled}
          aria-label="Message"
          className="flex-1"
        />
        <Button
          type="submit"
          size="sm"
          disabled={isSending || isDisabled || value.trim() === ""}
        >
          {isSending ? "Sending…" : "Send"}
        </Button>
      </div>
    </form>
  );
}
