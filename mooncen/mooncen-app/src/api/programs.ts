import { useInfiniteQuery, useQuery } from "@tanstack/react-query";

import {
  fetchCourse,
  fetchCourses,
  fetchNearbyBranches,
  type CourseListQuery,
  type NearbyBranchesQuery,
} from "./mooncenApi";

export const programQueryKeys = {
  all: ["courses"] as const,
  list: (query: CourseListQuery) => ["courses", "list", query] as const,
  infinite: (query: Omit<CourseListQuery, "page">) =>
    ["courses", "infinite", query] as const,
  detail: (id: string) => ["courses", "detail", id] as const,
  nearbyBranches: (query: NearbyBranchesQuery) =>
    ["branches", "nearby", query] as const,
};

export function useCourseList(query: CourseListQuery, enabled = true) {
  return useQuery({
    queryKey: programQueryKeys.list(query),
    queryFn: ({ signal }) => fetchCourses(query, { signal }),
    enabled,
  });
}

export function useInfiniteCourses(
  query: Omit<CourseListQuery, "page">,
  enabled = true,
) {
  return useInfiniteQuery({
    queryKey: programQueryKeys.infinite(query),
    initialPageParam: 1,
    queryFn: ({ pageParam, signal }) =>
      fetchCourses({ ...query, page: pageParam }, { signal }),
    getNextPageParam: (lastPage) => {
      const loaded = lastPage.page * lastPage.size;
      return loaded < lastPage.total ? lastPage.page + 1 : undefined;
    },
    enabled,
  });
}

export function useCourse(courseId: string) {
  const normalizedId = courseId.trim();
  return useQuery({
    queryKey: programQueryKeys.detail(normalizedId),
    queryFn: ({ signal }) => fetchCourse(normalizedId, { signal }),
    enabled: normalizedId.length > 0,
  });
}

export function useNearbyBranches(query: NearbyBranchesQuery, enabled = true) {
  return useQuery({
    queryKey: programQueryKeys.nearbyBranches(query),
    queryFn: ({ signal }) => fetchNearbyBranches(query, { signal }),
    enabled,
  });
}
