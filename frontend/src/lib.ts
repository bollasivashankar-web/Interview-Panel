import { createClient } from "@supabase/supabase-js";
import { assertPublicSupabaseKey } from "./public-config";
import { apiErrorMessage } from "./api-error";

const supabaseUrl = import.meta.env.VITE_SUPABASE_URL;
const supabaseKey = import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY;

if (!supabaseUrl || !supabaseKey) {
  throw new Error("VITE_SUPABASE_URL and VITE_SUPABASE_PUBLISHABLE_KEY are required");
}

assertPublicSupabaseKey(supabaseKey);
export const supabase = createClient(supabaseUrl, supabaseKey);
const apiUrl = import.meta.env.VITE_API_URL ?? "http://localhost:8000";
const e2eAccessToken = import.meta.env.DEV ? import.meta.env.VITE_E2E_ACCESS_TOKEN : undefined;
export const hasE2ESession = Boolean(e2eAccessToken);

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const { data } = await supabase.auth.getSession();
  const accessToken = e2eAccessToken ?? data.session?.access_token;
  if (!accessToken) throw new Error("Your session expired. Sign in again.");
  const response = await fetch(`${apiUrl}${path}`, {
    ...init,
    headers: {
      ...(init.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
      Authorization: `Bearer ${accessToken}`,
      ...init.headers,
    },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(apiErrorMessage(body));
  }
  return response.json();
}

