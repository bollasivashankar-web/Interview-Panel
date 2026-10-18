import { describe, expect, it } from "vitest";
import { parseTheme } from "./theme";

describe("theme preference", () => {
  it("uses the system preference when a saved choice is absent or invalid", () => {
    for (const value of [null, undefined, "unexpected", "system"]) expect(parseTheme(value)).toBe("system");
    expect(parseTheme("dark")).toBe("dark");
    expect(parseTheme("light")).toBe("light");
  });
});
