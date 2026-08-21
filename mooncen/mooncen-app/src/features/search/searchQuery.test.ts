import { describe, expect, it } from "vitest";

import { buildCourseSearchQuery } from "./searchQuery";

describe("buildCourseSearchQuery", () => {
  it("uses the backend 영유아 alias and excludes explicitly unavailable OPEN rows", () => {
    expect(
      buildCourseSearchQuery("", "education", ["OPEN", "INFANT"], "latest"),
    ).toMatchObject({
      scope: "education",
      statuses: ["OPEN", "DEADLINE"],
      excludeUnavailable: true,
      ageGroups: ["영유아"],
    });
  });

  it("does not request availability exclusion after OPEN is cleared", () => {
    const query = buildCourseSearchQuery(" 미술 ", "all", ["CHILD"], "popular");

    expect(query.keyword).toBe(" 미술 ");
    expect(query.scope).toBeUndefined();
    expect(query.statuses).toBeUndefined();
    expect(query.excludeUnavailable).toBe(false);
    expect(query.ageGroups).toEqual(["CHILD"]);
  });

  it("deduplicates the free quick filter and advanced fee filter", () => {
    const query = buildCourseSearchQuery(
      "",
      "provider",
      ["FREE"],
      "price_asc",
      undefined,
      [],
      [],
      ["free", "under50000"],
    );

    expect(query.feeGroups).toEqual(["free", "under50000"]);
  });
});
