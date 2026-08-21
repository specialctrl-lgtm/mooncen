import { useQueries, useQueryClient } from "@tanstack/react-query";
import { BookmarkCheck } from "lucide-react-native";
import { useMemo } from "react";
import {
  ActivityIndicator,
  FlatList,
  RefreshControl,
  StyleSheet,
  Text,
  View,
} from "react-native";

import { fetchCourse } from "../../api/mooncenApi";
import { programQueryKeys } from "../../api/programs";
import { AppHeader } from "../../components/AppHeader";
import { EmptyState } from "../../components/EmptyState";
import { ProgramCard } from "../../components/ProgramCard";
import { theme } from "../../constants/theme";
import { useFavoriteStore } from "../../stores/favoriteStore";

const MAX_GUEST_FAVORITES_TO_REFRESH = 50;

export function FavoritesScreen() {
  const favoriteIds = useFavoriteStore((state) => state.favoriteIds);
  const queryClient = useQueryClient();
  const visibleIds = favoriteIds.slice(0, MAX_GUEST_FAVORITES_TO_REFRESH);
  const courseQueries = useQueries({
    queries: visibleIds.map((id) => ({
      queryKey: programQueryKeys.detail(id),
      queryFn: ({ signal }: { signal: AbortSignal }) => fetchCourse(id, { signal }),
      staleTime: 5 * 60 * 1000,
    })),
  });
  const courses = useMemo(
    () => courseQueries.flatMap((query) => query.data ? [query.data] : []),
    [courseQueries],
  );
  const isLoading = favoriteIds.length > 0 && courseQueries.some((query) => query.isLoading);
  const isRefreshing = courseQueries.some((query) => query.isRefetching);
  const allFailed = courseQueries.length > 0 && courseQueries.every((query) => query.isError);

  function refresh() {
    void queryClient.invalidateQueries({ queryKey: programQueryKeys.all });
  }

  return (
    <FlatList
      contentContainerStyle={[styles.content, courses.length === 0 && styles.emptyContent]}
      data={courses}
      keyExtractor={(course) => course.id}
      ListHeaderComponent={
        <>
          <AppHeader
            title="보관함"
            subtitle="찜한 강좌를 기기에 안전하게 저장하고 운영 상태를 다시 확인해요."
          />
          <View style={styles.summaryCard}>
            <View style={styles.summaryIcon}>
              <BookmarkCheck color={theme.colors.primaryStrong} size={23} />
            </View>
            <View style={styles.summaryCopy}>
              <Text style={styles.summaryValue}>{favoriteIds.length.toLocaleString("ko-KR")}개</Text>
              <Text style={styles.summaryLabel}>찜한 강좌</Text>
            </View>
            <Text style={styles.summaryNotice}>로그인 없이 이 기기에 저장</Text>
          </View>
          {favoriteIds.length > MAX_GUEST_FAVORITES_TO_REFRESH ? (
            <Text style={styles.limitNotice}>
              최근 {MAX_GUEST_FAVORITES_TO_REFRESH}개 강좌의 운영 상태를 표시하고 있어요.
            </Text>
          ) : null}
        </>
      }
      ListEmptyComponent={
        isLoading ? (
          <ActivityIndicator
            accessibilityLabel="찜한 강좌 불러오는 중"
            color={theme.colors.primary}
            style={styles.loading}
          />
        ) : allFailed ? (
          <EmptyState
            title="찜한 강좌를 불러오지 못했어요"
            description="네트워크 상태를 확인하고 다시 시도해 주세요."
            actionLabel="다시 불러오기"
            onAction={refresh}
          />
        ) : (
          <EmptyState
            title="아직 찜한 강좌가 없어요"
            description="강좌 카드의 하트를 누르면 이곳에서 다시 확인할 수 있어요."
          />
        )
      }
      refreshControl={
        <RefreshControl
          colors={[theme.colors.primary]}
          onRefresh={refresh}
          refreshing={isRefreshing}
          tintColor={theme.colors.primary}
        />
      }
      renderItem={({ item }) => (
        <View style={styles.cardWrap}>
          <ProgramCard course={item} />
        </View>
      )}
      showsVerticalScrollIndicator={false}
      style={styles.screen}
    />
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: theme.colors.background },
  content: { paddingBottom: theme.spacing.xxl },
  emptyContent: { flexGrow: 1 },
  summaryCard: {
    minHeight: 78,
    flexDirection: "row",
    alignItems: "center",
    gap: theme.spacing.md,
    marginHorizontal: theme.spacing.lg,
    marginBottom: theme.spacing.lg,
    borderRadius: theme.radius.md,
    borderWidth: 1,
    borderColor: theme.colors.border,
    backgroundColor: theme.colors.surface,
    padding: theme.spacing.md,
  },
  summaryIcon: {
    width: 44,
    height: 44,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: theme.radius.md,
    backgroundColor: theme.colors.primarySoft,
  },
  summaryCopy: { flex: 1 },
  summaryValue: { color: theme.colors.text, fontSize: 19, fontWeight: "900" },
  summaryLabel: { color: theme.colors.textMuted, fontSize: 10 },
  summaryNotice: { color: theme.colors.primaryStrong, fontSize: 10, fontWeight: "800" },
  limitNotice: {
    color: theme.colors.warning,
    fontSize: 11,
    lineHeight: 17,
    paddingHorizontal: theme.spacing.lg,
    paddingBottom: theme.spacing.md,
  },
  cardWrap: { paddingHorizontal: theme.spacing.lg, paddingBottom: theme.spacing.md },
  loading: { marginTop: theme.spacing.xxl },
});
