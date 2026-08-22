import { describe, expect, it } from "vitest";

import { MooncenApiError } from "./mooncenApi";
import { shouldRetryQuery } from "./queryClient";

describe("shouldRetryQuery", () => {
  it("does not retry deterministic HTTP failures, including 500", () => {
    expect(shouldRetryQuery(0, new MooncenApiError("server error", 500))).toBe(false);
    expect(shouldRetryQuery(0, new MooncenApiError("invalid request", 422))).toBe(false);
  });

  it("allows one retry for transient gateway failures", () => {
    expect(shouldRetryQuery(0, new MooncenApiError("unavailable", 503))).toBe(true);
    expect(shouldRetryQuery(1, new MooncenApiError("unavailable", 503))).toBe(false);
  });

  it("does not retry an internally bounded timeout", () => {
    expect(shouldRetryQuery(0, new MooncenApiError("timeout"))).toBe(false);
  });

  it("allows only one retry for retryable transport failures", () => {
    expect(shouldRetryQuery(0, new MooncenApiError("network", null, true))).toBe(true);
    expect(shouldRetryQuery(1, new MooncenApiError("network", null, true))).toBe(false);
    expect(shouldRetryQuery(0, new Error("network"))).toBe(true);
    expect(shouldRetryQuery(1, new Error("network"))).toBe(false);
  });

  it("never retries an explicit cancellation", () => {
    const abortError = Object.assign(new Error("aborted"), { name: "AbortError" });
    expect(shouldRetryQuery(0, abortError)).toBe(false);
  });
});
