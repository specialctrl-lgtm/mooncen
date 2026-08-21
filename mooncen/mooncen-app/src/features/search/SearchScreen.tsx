import { ListFilter, RotateCcw, Search, SlidersHorizontal } from "lucide-react-native";
import { useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator,
  FlatList,
  Keyboard,
  Platform,
  Pressable,
  RefreshControl,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";

import type { CourseSort } from "../../api/mooncenApi";
import { useInfiniteCourses } from "../../api/programs";
import { AppHeader } from "../../components/AppHeader";
import { AdvancedFilterModal } from "../../components/AdvancedFilterModal";
import { CategoryTabs } from "../../components/CategoryTabs";
import { EmptyState } from "../../components/EmptyState";
import { FilterChip } from "../../components/FilterChip";
import { ProgramCard } from "../../components/ProgramCard";
import { theme } from "../../constants/theme";
import {
  type ProgramFilter,
  type ScopeFilter,
  useFilterStore,
} from "../../stores/filterStore";
import { getCourseApplicationState } from "../../utils/courseStatus";
import { buildCourseSearchQuery } from "./searchQuery";

const scopeOptions: Array<{ label: string; value: ScopeFilter }> = [
  { label: "전체", value: "all" },
  { label: "문화센터", value: "provider" },
  { label: "전시·체험", value: "experience" },
  { label: "평생교육", value: "education" },
];

const quickFilters: Array<{ label: string; value: ProgramFilter }> = [
  { label: "접수중", value: "OPEN" },
  { label: "무료", value: "FREE" },
  { label: "영유아", value: "INFANT" },
  { label: "어린이", value: "CHILD" },
  { label: "청소년", value: "TEEN" },
  { label: "성인", value: "ADULT" },
  { label: "시니어", value: "SENIOR" },
];

const sortModes: Array<{ label: string; value: CourseSort }> = [
  { label: "최신순", value: "latest" },
  { label: "인기순", value: "popular" },
  { label: "마감순", value: "deadline" },
  { label: "낮은 가격순", value: "price_asc" },
];

function useDebouncedValue(value: string, delayMs: number): string {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(timer);
  }, [delayMs, value]);
  return debounced;
}

export function SearchScreen() {
  const [filterVisible, setFilterVisible] = useState(false);
  const selectedScope = useFilterStore((state) => state.selectedScope);
  const searchText = useFilterStore((state) => state.searchText);
  const selectedBranchId = useFilterStore((state) => state.selectedBranchId);
  const selectedBranchLabel = useFilterStore((state) => state.selectedBranchLabel);
  const selectedFilters = useFilterStore((state) => state.selectedFilters);
  const selectedDays = useFilterStore((state) => state.selectedDays);
  const selectedTimeGroups = useFilterStore((state) => state.selectedTimeGroups);
  const selectedFeeGroups = useFilterStore((state) => state.selectedFeeGroups);
  const sort = useFilterStore((state) => state.sort);
  const setSelectedScope = useFilterStore((state) => state.setSelectedScope);
  const setSearchText = useFilterStore((state) => state.setSearchText);
  const setSelectedBranch = useFilterStore((state) => state.setSelectedBranch);
  const setSort = useFilterStore((state) => state.setSort);
  const toggleFilter = useFilterStore((state) => state.toggleFilter);
  const clearFilters = useFilterStore((state) => state.clearFilters);
  const debouncedSearch = useDebouncedValue(searchText.trim(), 350);
  const query = useMemo(
    () => buildCourseSearchQuery(
      debouncedSearch,
      selectedScope,
      selectedFilters,
      sort,
      selectedBranchId,
      selectedDays,
      selectedTimeGroups,
      selectedFeeGroups,
    ),
    [
      debouncedSearch,
      selectedBranchId,
      selectedDays,
      selectedFeeGroups,
      selectedFilters,
      selectedScope,
      selectedTimeGroups,
      sort,
    ],
  );
  const hasTooShortKeyword = debouncedSearch.length === 1;
  const coursesQuery = useInfiniteCourses(query, !hasTooShortKeyword);
  const courses = useMemo(() => {
    const seen = new Set<string>();
    return (coursesQuery.data?.pages ?? []).flatMap((page) =>
      page.items.filter((course) => {
        if (seen.has(course.id)) return false;
        seen.add(course.id);
        if (selectedFilters.includes("OPEN")) {
          const status = getCourseApplicationState(course).status;
          if (status !== "OPEN" && status !== "DEADLINE") return false;
        }
        return true;
      }),
    );
  }, [coursesQuery.data?.pages, selectedFilters]);
  const total = coursesQuery.data?.pages[0]?.total ?? 0;
  const sortLabel = sortModes.find((mode) => mode.value === sort)?.label ?? "정렬";
  const advancedFilterCount = selectedDays.length + selectedTimeGroups.length + selectedFeeGroups.length;

  useEffect(() => {
    const loadedPageCount = coursesQuery.data?.pages.length ?? 0;
    if (
      selectedFilters.includes("OPEN") &&
      courses.length < 5 &&
      loadedPageCount > 0 &&
      loadedPageCount < 5 &&
      coursesQuery.hasNextPage &&
      !coursesQuery.isFetchingNextPage
    ) {
      void coursesQuery.fetchNextPage();
    }
  }, [
    courses.length,
    coursesQuery.data?.pages.length,
    coursesQuery.fetchNextPage,
    coursesQuery.hasNextPage,
    coursesQuery.isFetchingNextPage,
    selectedFilters,
  ]);

  function cycleSortMode() {
    const index = sortModes.findIndex((mode) => mode.value === sort);
    setSort(sortModes[(index + 1) % sortModes.length].value);
  }

  function reset() {
    Keyboard.dismiss();
    clearFilters();
  }

  const listHeader = (
    <>
      <AppHeader title="강좌찾기" subtitle="전국의 문화센터·체험·교육을 실제 운영 데이터로 찾아보세요." />

      <View style={styles.searchBox}>
        <Search color={theme.colors.primaryStrong} size={20} strokeWidth={2.3} />
        <TextInput
          accessibilityHint="두 글자 이상 입력하면 서버에서 검색합니다"
          accessibilityLabel="강좌 검색어"
          autoCapitalize="none"
          autoCorrect={false}
          onChangeText={setSearchText}
          onSubmitEditing={() => Keyboard.dismiss()}
          placeholder="강좌명, 기관명, 지역 (두 글자 이상)"
          placeholderTextColor={theme.colors.textSoft}
          returnKeyType="search"
          style={styles.input}
          value={searchText}
        />
        {searchText.length > 0 ? (
          <Pressable
            accessibilityLabel="검색어 지우기"
            accessibilityRole="button"
            hitSlop={6}
            onPress={() => setSearchText("")}
            style={({ pressed }) => [styles.inputClear, pressed && styles.pressed]}
          >
            <Text style={styles.inputClearText}>×</Text>
          </Pressable>
        ) : null}
      </View>
      {hasTooShortKeyword ? (
        <Text accessibilityLiveRegion="polite" style={styles.searchHint}>
          검색어를 한 글자 더 입력해 주세요.
        </Text>
      ) : null}
      {selectedBranchId && selectedBranchLabel ? (
        <View style={styles.branchFilterRow}>
          <Text numberOfLines={1} style={styles.branchFilterText}>{selectedBranchLabel} 강좌만 보기</Text>
          <Pressable
            accessibilityLabel="기관 필터 해제"
            accessibilityRole="button"
            onPress={() => setSelectedBranch(undefined, undefined)}
            style={({ pressed }) => [styles.branchFilterClear, pressed && styles.pressed]}
          >
            <Text style={styles.branchFilterClearText}>해제</Text>
          </Pressable>
        </View>
      ) : null}

      <CategoryTabs options={scopeOptions} value={selectedScope} onChange={setSelectedScope} />

      <FlatList
        accessibilityLabel="빠른 검색 필터"
        contentContainerStyle={styles.filterWrap}
        data={quickFilters}
        horizontal
        keyExtractor={(item) => item.value}
        renderItem={({ item }) => (
          <FilterChip
            label={item.label}
            selected={selectedFilters.includes(item.value)}
            onPress={() => toggleFilter(item.value)}
          />
        )}
        showsHorizontalScrollIndicator={false}
      />

      <View style={styles.resultHeader}>
        <View style={styles.resultCopy}>
          <Text accessibilityLiveRegion="polite" style={styles.resultTitle}>
            {hasTooShortKeyword
              ? "검색어 입력 중"
              : selectedFilters.includes("OPEN")
                ? `검증해 불러온 ${courses.length.toLocaleString("ko-KR")}개`
                : `총 ${total.toLocaleString("ko-KR")}개`}
          </Text>
          <Text style={styles.resultDescription}>
            {selectedFilters.includes("OPEN")
              ? `서버 후보 ${total.toLocaleString("ko-KR")}개에서 만료·예약불가를 제외해요.`
              : "페이지 단위로 불러와 빠르게 보여드려요."}
          </Text>
        </View>
        <Pressable
          accessibilityLabel={`상세 필터${advancedFilterCount ? ` ${advancedFilterCount}개 적용 중` : ""}`}
          accessibilityRole="button"
          onPress={() => setFilterVisible(true)}
          style={({ pressed }) => [
            styles.utilityButton,
            advancedFilterCount > 0 && styles.utilityButtonSelected,
            pressed && styles.pressed,
          ]}
        >
          <SlidersHorizontal
            color={advancedFilterCount > 0 ? theme.colors.surface : theme.colors.primaryStrong}
            size={17}
          />
        </Pressable>
        <Pressable
          accessibilityLabel="검색 조건 초기화"
          accessibilityRole="button"
          onPress={reset}
          style={({ pressed }) => [styles.utilityButton, pressed && styles.pressed]}
        >
          <RotateCcw color={theme.colors.textMuted} size={16} />
        </Pressable>
        <Pressable
          accessibilityHint="누를 때마다 정렬 기준이 변경됩니다"
          accessibilityLabel={`정렬 ${sortLabel}`}
          accessibilityRole="button"
          onPress={cycleSortMode}
          style={({ pressed }) => [styles.sortButton, pressed && styles.pressed]}
        >
          <ListFilter color={theme.colors.primaryStrong} size={16} />
          <Text style={styles.sortText}>{sortLabel}</Text>
        </Pressable>
      </View>
    </>
  );

  return (
    <>
      <FlatList
      contentContainerStyle={[styles.content, courses.length === 0 && styles.emptyContent]}
      data={courses}
      initialNumToRender={8}
      keyboardDismissMode={Platform.OS === "ios" ? "interactive" : "on-drag"}
      keyboardShouldPersistTaps="handled"
      keyExtractor={(course) => course.id}
      ListEmptyComponent={
        hasTooShortKeyword ? null : coursesQuery.isLoading ? (
          <ActivityIndicator
            accessibilityLabel="강좌 검색 결과 불러오는 중"
            color={theme.colors.primary}
            style={styles.loading}
          />
        ) : coursesQuery.isError ? (
          <EmptyState
            title="검색 결과를 불러오지 못했어요"
            description="네트워크 상태를 확인하고 다시 시도해 주세요."
            actionLabel="다시 불러오기"
            onAction={() => void coursesQuery.refetch()}
          />
        ) : (
          <EmptyState
            title="검색 결과가 없어요"
            description="검색어를 줄이거나 조건을 초기화해 보세요."
            actionLabel="조건 초기화"
            onAction={reset}
          />
        )
      }
      ListFooterComponent={
        coursesQuery.isFetchingNextPage ? (
          <ActivityIndicator color={theme.colors.primary} style={styles.footerLoading} />
        ) : courses.length > 0 && !coursesQuery.hasNextPage ? (
          <Text style={styles.endText}>모든 결과를 확인했어요.</Text>
        ) : null
      }
      ListHeaderComponent={listHeader}
      onEndReached={() => {
        if (coursesQuery.hasNextPage && !coursesQuery.isFetchingNextPage) {
          void coursesQuery.fetchNextPage();
        }
      }}
      onEndReachedThreshold={0.45}
      refreshControl={
        <RefreshControl
          colors={[theme.colors.primary]}
          onRefresh={() => void coursesQuery.refetch()}
          refreshing={coursesQuery.isRefetching && !coursesQuery.isFetchingNextPage}
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
      <AdvancedFilterModal
        onClose={() => setFilterVisible(false)}
        resultCount={total}
        visible={filterVisible}
      />
    </>
  );
}

const styles = StyleSheet.create({
  screen: {
    flex: 1,
    backgroundColor: theme.colors.background,
  },
  content: {
    paddingBottom: theme.spacing.xxl,
  },
  emptyContent: {
    flexGrow: 1,
  },
  searchBox: {
    minHeight: 54,
    flexDirection: "row",
    alignItems: "center",
    marginHorizontal: theme.spacing.lg,
    borderRadius: theme.radius.md,
    borderWidth: 1,
    borderColor: theme.colors.border,
    backgroundColor: theme.colors.surface,
    paddingHorizontal: theme.spacing.md,
    ...theme.shadow,
  },
  input: {
    flex: 1,
    minHeight: 52,
    color: theme.colors.text,
    fontSize: 14,
    paddingHorizontal: theme.spacing.sm,
    paddingVertical: 0,
  },
  inputClear: {
    width: 44,
    height: 44,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: theme.radius.pill,
  },
  inputClearText: {
    color: theme.colors.textMuted,
    fontSize: 24,
    lineHeight: 26,
  },
  searchHint: {
    color: theme.colors.warning,
    fontSize: 11,
    fontWeight: "700",
    paddingHorizontal: theme.spacing.lg,
    paddingTop: theme.spacing.sm,
  },
  branchFilterRow: {
    minHeight: 40,
    flexDirection: "row",
    alignItems: "center",
    gap: theme.spacing.sm,
    marginHorizontal: theme.spacing.lg,
    marginTop: theme.spacing.sm,
    borderRadius: theme.radius.md,
    backgroundColor: theme.colors.primarySoft,
    paddingHorizontal: theme.spacing.md,
  },
  branchFilterText: {
    flex: 1,
    color: theme.colors.primaryStrong,
    fontSize: 11,
    fontWeight: "800",
  },
  branchFilterClear: { minWidth: 44, minHeight: 36, alignItems: "center", justifyContent: "center" },
  branchFilterClearText: { color: theme.colors.primaryStrong, fontSize: 11, fontWeight: "900" },
  filterWrap: {
    gap: theme.spacing.sm,
    paddingHorizontal: theme.spacing.lg,
    paddingVertical: theme.spacing.sm,
  },
  resultHeader: {
    minHeight: 72,
    flexDirection: "row",
    alignItems: "center",
    gap: theme.spacing.sm,
    paddingHorizontal: theme.spacing.lg,
    paddingVertical: theme.spacing.sm,
  },
  resultCopy: {
    flex: 1,
  },
  resultTitle: {
    color: theme.colors.text,
    fontSize: 16,
    fontWeight: "900",
  },
  resultDescription: {
    color: theme.colors.textMuted,
    fontSize: 10,
    marginTop: 2,
  },
  utilityButton: {
    width: 44,
    height: 44,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: theme.radius.pill,
    borderWidth: 1,
    borderColor: theme.colors.border,
    backgroundColor: theme.colors.surface,
  },
  utilityButtonSelected: {
    borderColor: theme.colors.primary,
    backgroundColor: theme.colors.primary,
  },
  sortButton: {
    minHeight: 44,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 5,
    borderRadius: theme.radius.pill,
    borderWidth: 1,
    borderColor: theme.colors.border,
    backgroundColor: theme.colors.surface,
    paddingHorizontal: theme.spacing.md,
  },
  sortText: {
    color: theme.colors.text,
    fontSize: 11,
    fontWeight: "800",
  },
  cardWrap: {
    paddingHorizontal: theme.spacing.lg,
    paddingBottom: theme.spacing.md,
  },
  loading: {
    marginTop: theme.spacing.xxl,
  },
  footerLoading: {
    marginVertical: theme.spacing.xl,
  },
  endText: {
    color: theme.colors.textSoft,
    fontSize: 11,
    textAlign: "center",
    paddingVertical: theme.spacing.xl,
  },
  pressed: {
    opacity: 0.65,
  },
});
