/**
 * Typed client for the Trisheet backend.
 *
 * Transport only. No business logic, no formatting, no derivation of figures.
 * Every call resolves to a discriminated result — this client never throws for
 * an expected failure.
 */

import type {
  AnalysisDepth,
  ApiError,
  ChatSuggestions,
  ChatTurn,
  Report,
  ReportDocument,
  Resolution,
  TickerSuggestion,
} from "@/lib/types";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "";

/** Discriminated result. Callers must handle both arms. */
export type ApiResult<T> =
  | { ok: true; data: T }
  | { ok: false; error: ApiError };

const UNREACHABLE: ApiError = {
  code: "backend_unreachable",
  message: "The report service is not responding. Try again in a moment.",
  detail: null,
};

function isApiError(value: unknown): value is ApiError {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const candidate = value as Record<string, unknown>;
  return (
    typeof candidate.code === "string" && typeof candidate.message === "string"
  );
}

export async function apiRequest<T>(
  path: string,
  init?: RequestInit,
): Promise<ApiResult<T>> {
  if (API_BASE_URL === "") {
    return {
      ok: false,
      error: {
        code: "api_base_url_unset",
        message:
          "The report service address is not configured. Set NEXT_PUBLIC_API_BASE_URL.",
        detail: null,
      },
    };
  }

  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        ...init?.headers,
      },
    });
  } catch (cause) {
    return {
      ok: false,
      error: {
        ...UNREACHABLE,
        detail: cause instanceof Error ? cause.message : null,
      },
    };
  }

  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    return {
      ok: false,
      error: {
        code: "malformed_response",
        message: "The report service returned a response we could not read.",
        detail: `HTTP ${response.status}`,
      },
    };
  }

  if (!response.ok) {
    return {
      ok: false,
      error: isApiError(payload)
        ? payload
        : {
            code: "request_failed",
            message: "The report service could not complete this request.",
            detail: `HTTP ${response.status}`,
          },
    };
  }

  return { ok: true, data: payload as T };
}

/* ---------------------------------------------------------------------------
   Endpoints

   One function per backend route. Each states its own failure behaviour;
   nothing here throws.
   --------------------------------------------------------------------------- */

/**
 * Autocomplete against the EDGAR ticker index.
 *
 * Returns an empty list on any failure rather than a result type. A suggestion
 * list that cannot load is not an error the reader needs to act on — they can
 * still type a ticker and submit it, and the resolver has the final say.
 */
export async function searchTickers(
  query: string,
  limit: number,
): Promise<readonly TickerSuggestion[]> {
  const result = await apiRequest<{ suggestions: TickerSuggestion[] }>(
    `/resolve/suggest?q=${encodeURIComponent(query)}&limit=${limit}`,
  );
  return result.ok ? result.data.suggestions : [];
}

/**
 * Resolves free text to a filer. An ambiguous query is a successful response
 * carrying candidates, not a failure — the interface asks rather than guessing.
 */
export async function resolveTicker(
  query: string,
): Promise<ApiResult<Resolution>> {
  return apiRequest<Resolution>("/resolve", {
    method: "POST",
    body: JSON.stringify({ query }),
  });
}

/**
 * Queues a report. The run itself is followed on the progress screen.
 *
 * `periods` is only meaningful (and only sent) when `depth` is "custom" —
 * the API rejects a request that sets it for any other depth.
 *
 * `sourceUrls` are links the reader attached. They are read into cited
 * source notes and never become facts, so one cannot put a figure into the
 * financial highlights table — see `m13_sources` on the backend. Blank
 * entries are dropped rather than sent, the same normalisation
 * `sendChatMessage` applies to `pastedUrl`.
 */
export async function createReport(
  cik: string,
  ticker: string,
  depth: AnalysisDepth,
  periods: number | null = null,
  sourceUrls: readonly string[] = [],
): Promise<ApiResult<Report>> {
  return apiRequest<Report>("/reports", {
    method: "POST",
    body: JSON.stringify({
      cik,
      ticker,
      depth,
      periods: depth === "custom" ? periods : null,
      sourceUrls: sourceUrls
        .map((url) => url.trim())
        .filter((url) => url !== ""),
    }),
  });
}

export async function fetchReport(
  reportId: string,
): Promise<ApiResult<Report>> {
  return apiRequest<Report>(`/reports/${reportId}`, { cache: "no-store" });
}

/**
 * The assembled document. Only meaningful once the report is complete; the
 * backend returns a typed error before then.
 */
export async function fetchReportDocument(
  reportId: string,
): Promise<ApiResult<ReportDocument>> {
  return apiRequest<ReportDocument>(`/reports/${reportId}/document`, {
    cache: "no-store",
  });
}

/**
 * Sends one message to the report's assistant and returns the reply turn.
 *
 * The backend answers 404 when the report does not exist, 409 when it has
 * not finished generating yet, and 503 when the assistant is unconfigured.
 * All three arrive as an ordinary `ApiError` — this function does not branch
 * on them, the caller renders `error.message` in the interface's voice.
 */
export async function sendChatMessage(
  reportId: string,
  message: string,
  pastedUrl?: string,
): Promise<ApiResult<ChatTurn>> {
  return apiRequest<ChatTurn>(`/reports/${reportId}/chat`, {
    method: "POST",
    body: JSON.stringify({
      message,
      pastedUrl: pastedUrl === undefined || pastedUrl === "" ? null : pastedUrl,
    }),
  });
}

/**
 * Example questions this report's own stored data can actually answer.
 * Deterministic on the backend — never a promise the assistant can't keep.
 */
export async function fetchChatSuggestions(
  reportId: string,
): Promise<ApiResult<ChatSuggestions>> {
  return apiRequest<ChatSuggestions>(`/reports/${reportId}/chat/suggestions`);
}
