"use client";

/**
 * The message field.
 *
 * Enter submits, Shift+Enter inserts a newline — the same convention as
 * every other chat surface, so it needs no instruction. `disabledReason` is
 * shown in place of the hint once the report isn't ready or the assistant
 * isn't configured; the field disables rather than accepting messages it
 * cannot answer.
 *
 * The URL field is the reader's own fallback, not a required step: read only
 * when the report's own data doesn't answer the question, per
 * `chat_agent.answer_question`. It stays a plain, explicit field rather than
 * sniffed out of the message text, so what was supplied is never ambiguous.
 */

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";

export function ChatComposer({
  onSubmit,
  isSending,
  disabledReason,
  turnsRemaining,
}: {
  onSubmit: (message: string, pastedUrl?: string) => void;
  isSending: boolean;
  /** Non-null once the conversation is gated: report not ready, assistant
   * unavailable, or the report could not be found. */
  disabledReason: string | null;
  /** Questions left in the current rate-limit window, or null when no
   * database is configured to count them. Shown so a limit reads as a
   * plain fact, not a surprise wall. */
  turnsRemaining: number | null;
}) {
  const [value, setValue] = useState("");
  const [pastedUrl, setPastedUrl] = useState("");
  const isDisabled = disabledReason !== null;

  const submit = () => {
    const trimmed = value.trim();
    if (trimmed === "" || isSending || isDisabled) {
      return;
    }
    const trimmedUrl = pastedUrl.trim();
    onSubmit(trimmed, trimmedUrl === "" ? undefined : trimmedUrl);
    setValue("");
    setPastedUrl("");
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
        {disabledReason ??
          (turnsRemaining === null
            ? "Asks only what this report's filed data can answer."
            : `Asks only what this report's filed data can answer. ${turnsRemaining} question${turnsRemaining === 1 ? "" : "s"} left.`)}
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

      <Input
        type="url"
        value={pastedUrl}
        onChange={(event) => {
          setPastedUrl(event.target.value);
        }}
        placeholder="Add a source: a company URL, if this report doesn't have it"
        disabled={isSending || isDisabled}
        aria-label="Company URL (optional)"
        className="mt-2 text-xs"
      />
    </form>
  );
}
