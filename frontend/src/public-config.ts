export function assertPublicSupabaseKey(value: string) {
  if (value.startsWith("sb_secret_")) throw new Error("A privileged Supabase key cannot be used in frontend configuration.");
  if (value.split(".").length === 3) {
    let role: unknown;
    try {
      const payload = value.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
      role = JSON.parse(atob(payload)).role;
    } catch { throw new Error("Invalid public Supabase key configuration."); }
    if (role !== "anon") throw new Error("Only a public anonymous legacy Supabase key can be used in the frontend.");
  }
}
