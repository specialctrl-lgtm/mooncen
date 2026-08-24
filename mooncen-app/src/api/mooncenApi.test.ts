import { afterEach, describe, expect, it, vi } from "vitest";

import {
  fetchCourse,
  fetchCourses,
  MooncenApiError,
} from "./mooncenApi";

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("mooncenApi", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("encodes course filters without losing Korean text or CSV values", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({ total: 0, page: 2, size: 40, items: [] }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await fetchCourses({
      page: 2,
      size: 40,
      keyword: " 어린이 미술 ",
      collectionCategories: ["체험", " 교육 "],
      serviceGroups: ["공공강좌", "체험"],
      domainCategories: ["예술/창작"],
      scope: "experience",
      provider: "MUNI_SEOUL",
      branchIds: ["branch-1", "branch-2"],
      lat: 37.5665,
      lon: 126.978,
      radiusKm: 10,
      ageGroups: ["영유아"],
      statuses: ["OPEN", "DEADLINE"],
      days: ["토", "일"],
      includeInactive: true,
      excludeUnavailable: true,
      sort: "deadline",
    });

    expect(fetchMock).toHaveBeenCalledOnce();
    const [requested, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const url = new URL(requested);

    expect(url.pathname).toBe("/api/courses/");
    expect(url.searchParams.get("page")).toBe("2");
    expect(url.searchParams.get("size")).toBe("40");
    expect(url.searchParams.get("keyword")).toBe("어린이 미술");
    expect(url.searchParams.get("collection_category")).toBe("체험,교육");
    expect(url.searchParams.get("service_group")).toBe("공공강좌,체험");
    expect(url.searchParams.get("domain_category")).toBe("예술/창작");
    expect(url.searchParams.get("scope")).toBe("experience");
    expect(url.searchParams.get("provider")).toBe("MUNI_SEOUL");
    expect(url.searchParams.get("branch_ids")).toBe("branch-1,branch-2");
    expect(url.searchParams.get("lat")).toBe("37.5665");
    expect(url.searchParams.get("lon")).toBe("126.978");
    expect(url.searchParams.get("radius_km")).toBe("10");
    expect(url.searchParams.get("age_groups")).toBe("영유아");
    expect(url.searchParams.get("statuses")).toBe("OPEN,DEADLINE");
    expect(url.searchParams.get("days")).toBe("토,일");
    expect(url.searchParams.get("include_inactive")).toBe("true");
    expect(url.searchParams.get("exclude_unavailable")).toBe("true");
    expect(url.searchParams.get("sort")).toBe("deadline");
    expect(requested).toContain("keyword=%EC%96%B4%EB%A6%B0%EC%9D%B4+%EB%AF%B8%EC%88%A0");
    expect(init.headers).toEqual({ Accept: "application/json" });
  });

  it("normalizes nullable and malformed optional course fields", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        id: " course-1 ",
        provider: null,
        title: " ",
        fee: "12000",
        material_fee: "not-a-number",
        reservation_available: "false",
        target_tags: [" 아동 ", null, ""],
        schedule_days: null,
        ai_tags: "not-an-array",
        branch: null,
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const course = await fetchCourse(" course/1 ");

    expect(course.id).toBe("course-1");
    expect(course.provider).toBe("UNKNOWN");
    expect(course.title).toBe("강좌명 미정");
    expect(course.fee).toBe(12000);
    expect(course.material_fee).toBeNull();
    expect(course.reservation_available).toBe(false);
    expect(course.target_tags).toEqual(["아동"]);
    expect(course.schedule_days).toEqual([]);
    expect(course.ai_tags).toEqual([]);
    expect(course.apply_start).toBeNull();
    expect(course.application_url).toBeNull();
    expect(course.raw_url).toBeNull();
    expect(course.branch).toBeNull();

    const requested = fetchMock.mock.calls[0]?.[0] as string;
    expect(new URL(requested).pathname).toBe("/api/courses/course%2F1");
  });

  it("surfaces a JSON HTTP error with its status and bounded detail", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({ detail: "검색어는 두 글자 이상 입력해 주세요." }, 422),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchCourses({ keyword: "가" })).rejects.toMatchObject({
      name: "MooncenApiError",
      status: 422,
      message: "검색어는 두 글자 이상 입력해 주세요.",
    } satisfies Partial<MooncenApiError>);
  });

  it("rejects a successful response whose body is not JSON", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response("<html>not an API response</html>", {
        status: 200,
        headers: { "Content-Type": "text/html" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchCourses()).rejects.toMatchObject({
      name: "MooncenApiError",
      status: 200,
      message: "서버 응답을 읽을 수 없습니다.",
    } satisfies Partial<MooncenApiError>);
  });

  it("forwards AbortSignal and preserves AbortError for cancelled requests", async () => {
    const abortError = Object.assign(new Error("aborted"), { name: "AbortError" });
    const fetchMock = vi.fn().mockImplementation((_input, init: RequestInit) => {
      const signal = init.signal as AbortSignal;
      return new Promise<Response>((_resolve, reject) => {
        if (signal.aborted) {
          reject(abortError);
          return;
        }
        signal.addEventListener("abort", () => reject(abortError), { once: true });
      });
    });
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();

    const request = fetchCourses({}, { signal: controller.signal });
    const requestSignal = (fetchMock.mock.calls[0]?.[1] as RequestInit).signal as AbortSignal;
    controller.abort();

    await expect(request).rejects.toBe(abortError);
    expect(requestSignal).not.toBe(controller.signal);
    expect(requestSignal.aborted).toBe(true);
  });

  it("aborts the underlying fetch when the internal request timeout expires", async () => {
    vi.useFakeTimers();
    const abortError = Object.assign(new Error("aborted"), { name: "AbortError" });
    let requestSignal: AbortSignal | undefined;
    const fetchMock = vi.fn().mockImplementation((_input, init: RequestInit) => {
      requestSignal = init.signal as AbortSignal;
      return new Promise<Response>((_resolve, reject) => {
        requestSignal?.addEventListener("abort", () => reject(abortError), { once: true });
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    const request = fetchCourses();
    const rejection = expect(request).rejects.toMatchObject({
      name: "MooncenApiError",
      status: null,
      message: "서버 응답 시간이 초과되었습니다. 다시 시도해 주세요.",
    } satisfies Partial<MooncenApiError>);

    await vi.advanceTimersByTimeAsync(15_000);
    await rejection;
    expect(requestSignal?.aborted).toBe(true);
  });

  it("keeps timeout and cancellation active while the response body is being read", async () => {
    vi.useFakeTimers();
    let requestSignal: AbortSignal | undefined;
    const fetchMock = vi.fn().mockImplementation((_input, init: RequestInit) => {
      requestSignal = init.signal as AbortSignal;
      return Promise.resolve({
        ok: true,
        status: 200,
        text: () => new Promise<string>(() => undefined),
      } as Response);
    });
    vi.stubGlobal("fetch", fetchMock);

    const request = fetchCourses();
    const rejection = expect(request).rejects.toMatchObject({
      name: "MooncenApiError",
      status: null,
    } satisfies Partial<MooncenApiError>);

    await vi.advanceTimersByTimeAsync(15_000);
    await rejection;
    expect(requestSignal?.aborted).toBe(true);
  });
});
