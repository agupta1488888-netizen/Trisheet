"use client";

/**
 * The assistant panel.
 *
 * Not a fourth grid column — a peer of the three-column layout that overlays
 * it, the way the provenance rail overlays the document on a narrow screen.
 * The report stays readable and interactive behind it: no dimming overlay,
 * no backdrop blur, just a hairline border in --rule, same as everything
 * else in this view.
 *
 * Layout:
 *  - `lg+`: a slim vertical "Ask" tab fixed to the right viewport edge
 *    slides a ~24rem panel in from the right.
 *  - below `lg`: `ProvenanceRail` already owns a `fixed inset-x-0 bottom-0`
 *    docked bar. This panel docks its own closed bar immediately above it —
 *    anchored `bottom-11` (2.75rem), which is the rail's own closed-button
 *    height (`py-3` + a `text-sm` line), so the two bars sit flush with a
 *    hairline between them and neither ever covers the other while both are
 *    closed. Opening this panel grows it upward from that same anchor, away
 *    from the rail. The one case this doesn't fully solve: if the rail's
 *    own panel is opened *while* this one is also open, the rail (which
 *    owns the higher `z-30`) can grow tall enough to paint over this
 *    panel's closed bar, since the rail's expanded height is unknown to
 *    this component. Given the rail and this panel are each meant to be
 *    opened one at a time in practice, that was judged an acceptable
 *    degrade rather than a reason to change `ProvenanceRail` itself.
 *
 * State (the transcript, the in-flight request) lives here, not in the
 * composer or the message list, because both the desktop and mobile
 * renderings share one conversation.
 */

import { useId, useState } from "react";

import { cn } from "@/lib/utils";
import { sendChatMessage } from "@/lib/api";
import type { ApiError, ChatTurn } from "@/lib/types";
import { ChatComposer } from "@/components/report/chat-composer";
import { ChatMessage } from "@/components/report/chat-message";

function nextTurnId(): string {
  return typeof crypto.randomUUID === "function"
    ? crypto.randomUUID()
    : `turn-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function Transcript({ turns }: { turns: readonly ChatTurn[] }) {
  if (turns.length === 0) {
    return (
      <p className="py-3 text-sm leading-relaxed text-muted-foreground">
        Ask a question about this report. Answers are grounded in the same
        filed data the report itself is built from.
      </p>
    );
  }

  return (
    <div>
      {turns.map((turn) => (
        <ChatMessage key={turn.id} turn={turn} />
      ))}
    </div>
  );
}

function GateNotice({ error }: { error: ApiError }) {
  return (
    <div className="border-t border-rule px-5 py-3">
      <p className="text-sm text-ink">{error.message}</p>
      {error.detail === null ? null : (
        <p className="ref mt-1 text-xs text-muted-foreground">
          {error.detail}
        </p>
      )}
    </div>
  );
}

export function ChatPanel({ reportId }: { reportId: string }) {
  const panelId = useId();
  const [isOpen, setIsOpen] = useState(false);
  const [turns, setTurns] = useState<readonly ChatTurn[]>([]);
  const [isSending, setIsSending] = useState(false);
  const [gateError, setGateError] = useState<ApiError | null>(null);

  const toggle = () => {
    setIsOpen((open) => !open);
  };

  const submit = async (message: string) => {
    const userTurn: ChatTurn = {
      id: nextTurnId(),
      role: "user",
      claims: [],
      content: message,
      notFound: false,
      createdAt: new Date().toISOString(),
    };
    setTurns((current) => [...current, userTurn]);
    setIsSending(true);

    const result = await sendChatMessage(reportId, message);
    setIsSending(false);

    if (!result.ok) {
      // 404 (no such report), 409 (still generating) and 503 (assistant
      // unconfigured) all arrive here as an ordinary ApiError. This gates
      // the composer rather than appending a broken turn — none of those
      // three resolve by asking again.
      setGateError(result.error);
      return;
    }

    setTurns((current) => [...current, result.data]);
  };

  const disabledReason = gateError?.message ?? null;

  return (
    <>
      {/* Wide: a finger tab that slides a panel in from the right edge. */}
      <button
        type="button"
        aria-expanded={isOpen}
        aria-controls={panelId}
        onClick={toggle}
        className={cn(
          "fixed top-1/2 z-40 hidden -translate-y-1/2 items-center rounded-l-xs border border-r-0 border-rule bg-paper px-2 py-5 text-xs font-medium tracking-wide text-ink transition-[right] motion-reduce:transition-none hover:bg-wash lg:flex",
          isOpen ? "right-96" : "right-0",
        )}
        style={{ writingMode: "vertical-rl" }}
      >
        Ask
      </button>

      <aside
        aria-label="Assistant"
        id={panelId}
        inert={!isOpen}
        className={cn(
          "fixed inset-y-0 right-0 z-40 hidden w-96 flex-col border-l border-rule bg-paper transition-transform duration-200 motion-reduce:transition-none lg:flex",
          isOpen ? "translate-x-0" : "translate-x-full",
        )}
      >
        <div className="flex items-baseline justify-between gap-2 border-b border-rule px-5 py-4">
          <h2 className="text-sm font-medium text-ink">Assistant</h2>
          <button
            type="button"
            onClick={toggle}
            className="ref text-xs text-muted-foreground hover:text-ink"
          >
            Close
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-5">
          <Transcript turns={turns} />
        </div>

        {gateError === null ? null : <GateNotice error={gateError} />}

        <ChatComposer
          onSubmit={(message) => {
            void submit(message);
          }}
          isSending={isSending}
          disabledReason={disabledReason}
        />
      </aside>

      {/* Narrow: a docked bar stacked immediately above the provenance
          rail's own docked bar, expanding upward when opened. */}
      <aside
        aria-label="Assistant"
        className="fixed inset-x-0 bottom-11 z-20 border-t border-rule bg-paper lg:hidden"
      >
        <div
          id={`${panelId}-mobile`}
          hidden={!isOpen}
          className="flex max-h-[60vh] flex-col"
        >
          <div className="flex-1 overflow-y-auto px-5">
            <Transcript turns={turns} />
          </div>

          {gateError === null ? null : <GateNotice error={gateError} />}

          <ChatComposer
            onSubmit={(message) => {
              void submit(message);
            }}
            isSending={isSending}
            disabledReason={disabledReason}
          />
        </div>

        <button
          type="button"
          aria-expanded={isOpen}
          aria-controls={`${panelId}-mobile`}
          onClick={toggle}
          className="flex w-full items-center justify-between gap-3 border-t border-rule px-5 py-3 text-left"
        >
          <span className="text-sm font-medium text-ink">Ask</span>
          <span
            aria-hidden="true"
            className="ref text-xs text-muted-foreground"
          >
            {isOpen ? "Close" : "Open"}
          </span>
        </button>
      </aside>
    </>
  );
}
