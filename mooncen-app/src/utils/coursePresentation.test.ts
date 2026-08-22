import { describe, expect, it } from "vitest";

import {
  formatCourseCapacity,
  formatCourseHeadcount,
  getCourseApplicationMethod,
} from "./coursePresentation";

describe("course capacity presentation", () => {
  it("does not turn an unknown current enrollment into zero", () => {
    expect(formatCourseCapacity(null, 8)).toBe("정원 8명");
    expect(formatCourseCapacity(0, 8)).toBe("0 / 8명");
  });

  it("keeps explicit zero and positive remaining or waitlist counts visible", () => {
    expect(formatCourseHeadcount(0)).toBe("0명");
    expect(formatCourseHeadcount(8)).toBe("8명");
    expect(formatCourseHeadcount(10)).toBe("10명");
  });

  it("hides unavailable or invalid capacity values", () => {
    expect(formatCourseCapacity(3, null)).toBeNull();
    expect(formatCourseCapacity(3, -1)).toBeNull();
    expect(formatCourseHeadcount(null)).toBeNull();
    expect(formatCourseHeadcount(-1)).toBeNull();
  });

  it("normalizes a reported application method for display", () => {
    expect(
      getCourseApplicationMethod({ application_method_raw: " 인터넷 ", application_type: "visit" }),
    ).toBe("인터넷");
    expect(
      getCourseApplicationMethod({ application_method_raw: " ", application_type: "ONLINE_LOGIN_REQUIRED" }),
    ).toBe("온라인 신청(로그인 필요)");
    expect(
      getCourseApplicationMethod({ application_method_raw: null, application_type: "IN_PERSON" }),
    ).toBe("방문 신청");
    expect(
      getCourseApplicationMethod({ application_method_raw: null, application_type: "UNKNOWN_CODE" }),
    ).toBeNull();
    expect(
      getCourseApplicationMethod({ application_method_raw: null, application_type: null }),
    ).toBeNull();
  });
});
