import { QueryClient } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import { sessionBoundary } from "./session";

describe("account cache isolation", () => {
  it("clears company and candidate data on sign-out and direct account switches", () => {
    const client = new QueryClient();
    const update = vi.fn();
    const change = sessionBoundary(client, update);
    change("alice");
    client.setQueryData(["company"], { name: "Alice Company" });
    client.setQueryData(["sessions"], [{ id: "alice-interview" }]);
    change(null);
    expect(client.getQueryCache().getAll()).toHaveLength(0);
    change("alice");
    client.setQueryData(["company"], { name: "Alice Company" });
    change("bob");
    expect(client.getQueryData(["company"])).toBeUndefined();
    expect(update).toHaveBeenLastCalledWith("bob");
  });

  it("retains cached data when the same user's token is refreshed", () => {
    const client = new QueryClient();
    const change = sessionBoundary(client, vi.fn());
    change("alice");
    client.setQueryData(["jobs"], ["saved-job"]);
    change("alice");
    expect(client.getQueryData(["jobs"])).toEqual(["saved-job"]);
  });

  it("prevents an old account's pending request from repopulating the cache", async () => {
    const client = new QueryClient();
    const change = sessionBoundary(client, vi.fn());
    change("alice");
    let resolve!: (value: string) => void;
    const request = client.fetchQuery({ queryKey: ["company"], queryFn: () => new Promise<string>((done) => { resolve = done; }) }).catch(() => undefined);
    change("bob");
    resolve("Alice Company");
    await request;
    expect(client.getQueryData(["company"])).toBeUndefined();
  });
});
