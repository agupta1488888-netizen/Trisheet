"use client";

/**
 * Plays a scripted pipeline run through the real `StepFeed`.
 *
 * The feed component is unchanged from the one the live screen uses — only the
 * source of the steps differs. That is the point: what you verify here is what
 * ships.
 */

import { useEffect, useState } from "react";

import type { ProgressFrame } from "@/lib/mock/progress";
import { StepFeed } from "@/components/progress/step-feed";

export function ScriptedStepFeed({
  ticker,
  script,
  errorMessage,
}: {
  ticker: string;
  script: readonly ProgressFrame[];
  errorMessage: string | null;
}) {
  const [frameIndex, setFrameIndex] = useState(0);

  useEffect(() => {
    const frame = script[frameIndex];
    if (frame === undefined || !Number.isFinite(frame.holdMs)) {
      return;
    }
    const timer = window.setTimeout(() => {
      setFrameIndex((current) => Math.min(current + 1, script.length - 1));
    }, frame.holdMs);
    return () => {
      window.clearTimeout(timer);
    };
  }, [frameIndex, script]);

  const frame = script[frameIndex] ?? script[script.length - 1];
  if (frame === undefined) {
    return null;
  }

  const isFinished = frameIndex === script.length - 1;

  return (
    <div>
      <StepFeed
        ticker={ticker}
        status={frame.status}
        steps={frame.steps}
        errorMessage={frame.status === "failed" ? errorMessage : null}
      />

      {isFinished ? (
        <button
          type="button"
          onClick={() => {
            setFrameIndex(0);
          }}
          className="mt-8 border border-rule px-4 py-2 text-sm text-ink hover:border-certified hover:text-certified"
        >
          Replay
        </button>
      ) : null}
    </div>
  );
}
