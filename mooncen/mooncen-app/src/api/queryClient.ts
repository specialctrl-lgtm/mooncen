import { QueryClient } from "@tanstack/react-query";

import { MooncenApiError } from "./mooncenApi";

export function shouldRetryQuery(failureCount: number, error: unknown): boolean {
  if (error instanceof Error && error.name === "AbortError") return false;
  if (error instanceof MooncenApiError) {
    if (error.status !== null) {
      return [502, 503, 504].includes(error.status) && failureCount < 1;
    }
    return error.retryable && failureCount < 1;
  }
  return failureCount < 1;
}

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 1000 * 60 * 5,
      gcTime: 1000 * 60 * 30,
      retry: shouldRetryQuery,
      refetchOnReconnect: true,
    },
  },
});
