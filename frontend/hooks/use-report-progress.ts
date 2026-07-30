"use client";

/**
 * The live progress feed.
 *
 * Steps arrive as `run_logs` rows over Supabase Realtime, correlated by
 * `report_id`. Status arrives from the `reports` row itself. Both channels are
 * read-only; there is no client write path anywhere in this product.
 *
 * Realtime is a convenience, not a dependency. When Supabase is unconfigured
 * or the channel fails to subscribe, the hook falls back to polling the
 * backend for status and reports `isLive: false`, so the interface can say
 * which mode it is in rather than appearing stuck.
 */

import { useEffect, useMemo, useRef, useState } from "react";

import { apiRequest } from "@/lib/api";
import { PROGRESS_POLL_INTERVAL_MS } from "@/lib/constants";
import { getSupabaseClient } from "@/lib/supabase";
import type { ProgressStep, Report, ReportStatus, StepState } from "@/lib/types";

const STEP_STATES: readonly StepState[] = [
  "pending",
  "running",
  "done",
  "skipped",
  "failed",
];

const REPORT_STATUSES: readonly ReportStatus[] = [
  "queued",
  "resolving",
  "extracting",
  "analysing",
  "writing",
  "verifying",
  "complete",
  "failed",
];

/** A `run_logs` row, as the browser receives it. */
interface RunLogRow {
  module: string;
  message: string;
  context: Record<string, unknown>;
  created_at: string;
}

/** A `reports` row, in database column casing. */
interface ReportRow {
  status: string;
  error_message: string | null;
}

export interface ReportProgress {
  status: ReportStatus;
  steps: readonly ProgressStep[];
  errorMessage: string | null;
  /** False when the feed is polling rather than streaming. */
  isLive: boolean;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function readString(source: Record<string, unknown>, key: string): string | null {
  const value = source[key];
  return typeof value === "string" ? value : null;
}

function readNumber(source: Record<string, unknown>, key: string): number | null {
  const value = source[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function toStepState(value: string | null): StepState {
  return value !== null && (STEP_STATES as readonly string[]).includes(value)
    ? (value as StepState)
    : "running";
}

function toReportStatus(value: string | null, fallback: ReportStatus): ReportStatus {
  return value !== null && (REPORT_STATUSES as readonly string[]).includes(value)
    ? (value as ReportStatus)
    : fallback;
}

/**
 * Turns a log row into a step, or null when the row is not a step event.
 *
 * The pipeline marks step events with a `step` key in `context`; everything
 * else in `run_logs` is diagnostic and does not belong in a reader-facing feed.
 */
function toStep(row: RunLogRow): ProgressStep | null {
  const step = row.context["step"];
  if (!isRecord(step)) {
    return null;
  }

  const count = readNumber(step, "count");

  return {
    module: row.module,
    label: readString(step, "label") ?? row.message,
    state: toStepState(readString(step, "state")),
    count,
    countLabel: count === null ? null : readString(step, "count_label"),
    at: row.created_at,
    detail: readString(step, "detail"),
  };
}

function parseRunLogRow(value: unknown): RunLogRow | null {
  if (!isRecord(value)) {
    return null;
  }
  // Not named `module`: that identifier is reserved in this bundler context.
  const moduleName = readString(value, "module");
  const createdAt = readString(value, "created_at");
  if (moduleName === null || createdAt === null) {
    return null;
  }
  const context = value["context"];
  return {
    module: moduleName,
    message: readString(value, "message") ?? moduleName,
    context: isRecord(context) ? context : {},
    created_at: createdAt,
  };
}

function parseReportRow(value: unknown): ReportRow | null {
  if (!isRecord(value)) {
    return null;
  }
  const status = readString(value, "status");
  if (status === null) {
    return null;
  }
  return { status, error_message: readString(value, "error_message") };
}

/**
 * Merges a step into the feed, keyed by module.
 *
 * A step is emitted when it starts and again when it settles, so the second
 * event replaces the first in place. That is what makes the feed a feed rather
 * than a duplicated list.
 */
function mergeStep(
  steps: readonly ProgressStep[],
  incoming: ProgressStep,
): ProgressStep[] {
  const existing = steps.findIndex((step) => step.module === incoming.module);
  if (existing === -1) {
    return [...steps, incoming];
  }
  const next = [...steps];
  next[existing] = incoming;
  return next;
}

const TERMINAL: readonly ReportStatus[] = ["complete", "failed"];

export function useReportProgress(
  reportId: string,
  initial: { status: ReportStatus; errorMessage: string | null },
): ReportProgress {
  const [status, setStatus] = useState<ReportStatus>(initial.status);
  const [errorMessage, setErrorMessage] = useState<string | null>(
    initial.errorMessage,
  );
  const [steps, setSteps] = useState<readonly ProgressStep[]>([]);
  const [isLive, setIsLive] = useState(false);

  // Read inside effects without making them re-run when the value changes.
  const statusRef = useRef(status);
  statusRef.current = status;

  const client = useMemo(() => getSupabaseClient(), []);

  // --- Realtime -----------------------------------------------------------
  useEffect(() => {
    if (client === null) {
      return;
    }

    let cancelled = false;

    // Backfill, so a reload mid-run does not start with an empty feed.
    void client
      .from("run_logs")
      .select("module, message, context, created_at")
      .eq("report_id", reportId)
      .order("created_at", { ascending: true })
      .then(({ data }) => {
        if (cancelled || data === null) {
          return;
        }
        setSteps((current) =>
          data.reduce<ProgressStep[]>((accumulated, row) => {
            const parsed = parseRunLogRow(row);
            const step = parsed === null ? null : toStep(parsed);
            return step === null ? accumulated : mergeStep(accumulated, step);
          }, [...current]),
        );
      });

    const channel = client
      .channel(`report:${reportId}`)
      .on(
        "postgres_changes",
        {
          event: "INSERT",
          schema: "public",
          table: "run_logs",
          filter: `report_id=eq.${reportId}`,
        },
        (payload) => {
          const parsed = parseRunLogRow(payload.new);
          const step = parsed === null ? null : toStep(parsed);
          if (step !== null) {
            setSteps((current) => mergeStep(current, step));
          }
        },
      )
      .on(
        "postgres_changes",
        {
          event: "UPDATE",
          schema: "public",
          table: "reports",
          filter: `id=eq.${reportId}`,
        },
        (payload) => {
          const row = parseReportRow(payload.new);
          if (row === null) {
            return;
          }
          setStatus((current) => toReportStatus(row.status, current));
          setErrorMessage(row.error_message);
        },
      )
      .subscribe((state) => {
        if (cancelled) {
          return;
        }
        setIsLive(state === "SUBSCRIBED");
      });

    return () => {
      cancelled = true;
      setIsLive(false);
      void client.removeChannel(channel);
    };
  }, [client, reportId]);

  // --- Polling fallback ---------------------------------------------------
  // Runs whenever the stream is not carrying status, including while a channel
  // is still connecting. Status is cheap to poll; steps are not, so a polled
  // run shows fewer lines rather than a fabricated feed.
  useEffect(() => {
    if (isLive || TERMINAL.includes(status)) {
      return;
    }

    let cancelled = false;

    const poll = async () => {
      const result = await apiRequest<Report>(`/reports/${reportId}`);
      if (cancelled || !result.ok) {
        return;
      }
      setStatus((current) => toReportStatus(result.data.status, current));
      setErrorMessage(result.data.errorMessage);
    };

    const timer = window.setInterval(() => {
      void poll();
    }, PROGRESS_POLL_INTERVAL_MS);

    void poll();

    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [isLive, reportId, status]);

  return { status, steps, errorMessage, isLive };
}
