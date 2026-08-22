import { describe, expect, it } from "vitest";

import {
  canApplyToCourse,
  getCourseApplicationState,
  getEffectiveCourseStatus,
  isCourseApplicationExpired,
  normalizeCourseStatus,
  resolveCourseApplicationUrl,
} from "./courseStatus";

const KST_REFERENCE = new Date("2026-08-09T00:30:00+09:00");

describe("courseStatus", () => {
  it("expires an OPEN course after apply_end yesterday in KST", () => {
    const course = {
      status: "OPEN",
      apply_end: "2026-08-08",
      reservation_available: true,
    };

    expect(isCourseApplicationExpired(course.apply_end, KST_REFERENCE)).toBe(true);
    expect(getEffectiveCourseStatus(course, KST_REFERENCE)).toBe("CLOSED");
  });

  it("keeps an OPEN course available through apply_end today in KST", () => {
    const course = {
      status: "OPEN",
      apply_end: "2026-08-09",
      reservation_available: true,
      application_url: "https://reserve.example/course/1",
    };

    expect(isCourseApplicationExpired(course.apply_end, KST_REFERENCE)).toBe(false);
    expect(getEffectiveCourseStatus(course, KST_REFERENCE)).toBe("OPEN");
    expect(canApplyToCourse(course, KST_REFERENCE)).toBe(true);
  });

  it("closes OPEN and DEADLINE when reservation is explicitly unavailable", () => {
    expect(
      getEffectiveCourseStatus(
        { status: "OPEN", apply_end: "2026-08-10", reservation_available: false },
        KST_REFERENCE,
      ),
    ).toBe("CLOSED");
    expect(
      getEffectiveCourseStatus(
        { status: "DEADLINE", apply_end: "2026-08-10", reservation_available: false },
        KST_REFERENCE,
      ),
    ).toBe("CLOSED");
  });

  it("normalizes unsupported statuses to UNKNOWN and preserves WAITING", () => {
    expect(normalizeCourseStatus(" waiting ")).toBe("WAITING");
    expect(normalizeCourseStatus("PAUSED")).toBe("UNKNOWN");
    expect(normalizeCourseStatus(null)).toBe("UNKNOWN");

    const waiting = getCourseApplicationState(
      {
        status: "WAITING",
        apply_end: "2026-08-08",
        reservation_available: false,
        application_url: "https://reserve.example/waitlist",
      },
      KST_REFERENCE,
    );
    expect(waiting.status).toBe("WAITING");
    expect(waiting.canApply).toBe(false);

    const availableWaitlist = getCourseApplicationState(
      {
        status: "WAITING",
        apply_end: "2026-08-08",
        reservation_available: true,
        application_url: "https://reserve.example/waitlist",
      },
      KST_REFERENCE,
    );
    expect(availableWaitlist.status).toBe("WAITING");
    expect(availableWaitlist.canApply).toBe(true);
  });

  it("uses only a safe application_url for the application CTA", () => {
    const validCourse = {
      status: "OPEN",
      apply_end: "2026-08-09",
      reservation_available: true,
      application_url: "https://reserve.example/apply?id=1",
      raw_url: "https://source.example/course/1",
    };
    expect(resolveCourseApplicationUrl(validCourse)).toBe(
      "https://reserve.example/apply?id=1",
    );
    expect(canApplyToCourse(validCourse, KST_REFERENCE)).toBe(true);

    const unsafeCourse = {
      ...validCourse,
      application_url: "https://user:password@reserve.example/apply",
    };
    expect(resolveCourseApplicationUrl(unsafeCourse)).toBeNull();
    expect(canApplyToCourse(unsafeCourse, KST_REFERENCE)).toBe(false);
  });

  it("rejects executable schemes and credential-bearing application URLs", () => {
    for (const application_url of [
      "javascript:alert(1)",
      "data:text/html,unsafe",
      "https://user:password@reserve.example/apply",
    ]) {
      const course = {
        status: "OPEN",
        apply_end: "2026-08-09",
        reservation_available: true,
        application_url,
        raw_url: null,
      };
      expect(resolveCourseApplicationUrl(course)).toBeNull();
      expect(canApplyToCourse(course, KST_REFERENCE)).toBe(false);
    }
  });

  it("keeps raw_url as a source link and never promotes it to an application CTA", () => {
    const sourceOnlyCourse = {
      status: "OPEN",
      apply_end: "2026-08-09",
      reservation_available: true,
      application_url: null,
      raw_url: "https://source.example/course/1",
    };

    expect(resolveCourseApplicationUrl(sourceOnlyCourse)).toBeNull();
    expect(getCourseApplicationState(sourceOnlyCourse, KST_REFERENCE)).toMatchObject({
      applicationUrl: null,
      canApply: false,
    });
  });

  it("does not enable an application CTA for CLOSED or UNKNOWN status", () => {
    const application_url = "https://reserve.example/course/1";
    expect(
      canApplyToCourse(
        { status: "CLOSED", reservation_available: true, application_url },
        KST_REFERENCE,
      ),
    ).toBe(false);
    expect(
      canApplyToCourse(
        { status: "unexpected", reservation_available: true, application_url },
        KST_REFERENCE,
      ),
    ).toBe(false);
  });
});
