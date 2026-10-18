import { expect, it } from "vitest";
import { apiErrorMessage } from "./api-error";

it("renders server validation errors with their field names", () => {
  expect(apiErrorMessage({ detail: [{ loc: ["body", "expires_at"], msg: "Must be in the future" }] }))
    .toBe("expires_at: Must be in the future");
});

it("retains ordinary API messages and handles unexpected error bodies", () => {
  expect(apiErrorMessage({ detail: "Invitation expired" })).toBe("Invitation expired");
  for (const body of [null, {}, { detail: {} }, { detail: [null, {}] }]) {
    expect(apiErrorMessage(body)).toBe("Request failed. Please try again.");
  }
});
