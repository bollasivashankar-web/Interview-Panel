import { describe, expect, it } from "vitest";
import { assertPublicSupabaseKey } from "./public-config";

describe("public Supabase key guard", () => {
  it("accepts public keys and rejects privileged keys before bundling", () => {
    expect(() => assertPublicSupabaseKey("sb_publishable_test")).not.toThrow();
    const token = (role: string) => `header.${btoa(JSON.stringify({ role }))}.signature`;
    expect(() => assertPublicSupabaseKey(token("anon"))).not.toThrow();
    expect(() => assertPublicSupabaseKey("sb_secret_test")).toThrow();
    expect(() => assertPublicSupabaseKey(token("service_role"))).toThrow();
    expect(() => assertPublicSupabaseKey("invalid.jwt.token")).toThrow();
  });
});
