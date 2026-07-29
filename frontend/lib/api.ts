/**
 * Typed client for the Tearsheet backend.
 *
 * Transport only. No business logic, no formatting, no derivation of figures.
 * Every call resolves to a discriminated result — this client never throws for
 * an expected failure.
 */

import type { ApiError } from "@/lib/types";

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
