/**
 * Supabase browser client.
 *
 * Read paths and realtime subscriptions only. Writes go through the backend so
 * that provenance enforcement cannot be bypassed from the client.
 */

import { createClient, type SupabaseClient } from "@supabase/supabase-js";

const SUPABASE_URL = process.env.NEXT_PUBLIC_SUPABASE_URL;
const SUPABASE_PUBLISHABLE_KEY =
  process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY;

let client: SupabaseClient | null = null;

/**
 * Returns the shared browser client, or null when Supabase is not configured.
 *
 * Callers must handle null: Supabase being unreachable degrades the live status
 * feed, it never blocks a report from rendering.
 */
export function getSupabaseClient(): SupabaseClient | null {
  if (!SUPABASE_URL || !SUPABASE_PUBLISHABLE_KEY) {
    return null;
  }
  if (client === null) {
    client = createClient(SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY, {
      auth: { persistSession: false },
    });
  }
  return client;
}
