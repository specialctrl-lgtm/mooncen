import { router } from "expo-router";
import {
  Baby,
  Building2,
  GraduationCap,
  LocateFixed,
  Search,
  Sparkles,
  UsersRound,
} from "lucide-react-native";
import type { LucideIcon } from "lucide-react-native";
import {
  ActivityIndicator,
  Image,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";

import { useCourseList } from "../../api/programs";
import type { CourseScope } from "../../api/mooncenApi";
import { AppHeader } from "../../components/AppHeader";
import { EmptyState } from "../../components/EmptyState";
import { ProgramCard } from "../../components/ProgramCard";
import { SectionHeader } from "../../components/SectionHeader";
import { theme } from "../../constants/theme";
import { ProgramFilter, useFilterStore } from "../../stores/filterStore";
import { useUserPreferenceStore } from "../../stores/userPreferenceStore";
import { getCourseApplicationState } from "../../utils/courseStatus";

const scopeCards: Array<{
  scope: CourseScope;
  title: string;
  description: string;
  icon: LucideIcon;
  color: string;
  backgroundColor: string;
}> = [
  {
    scope: "provider",
    title: "문화센터",
    description: "백화점·마트",
    icon: Building2,
    color: theme.colors.primaryStrong,
    backgroundColor: theme.colors.primarySoft,
  },
  {
    scope: "experience",
    title: "전시·체험",
    description: "가족의 특별한 하루",
    icon: Sparkles,
    color: theme.colors.accent,
    backgroundColor: theme.colors.accentSoft,
  },
  {
    scope: "education",
    title: "평생교육",
    description: "공공 강좌·배움",
    icon: GraduationCap,
    color: theme.colors.secondary,
    backgroundColor: theme.colors.secondarySoft,
  },
];

const audienceButtons: Array<{
  label: string;
  filter: ProgramFilter;
  icon: LucideIcon;
}> = [
  { label: "영유아", filter: "INFANT", icon: Baby },
  { label: "어린이", filter: "CHILD", icon: UsersRound },
  { label: "청소년", filter: "TEEN", icon: GraduationCap },
  { label: "성인", filter: "ADULT", icon: UsersRound },
];

export function HomeScreen() {
  const selectedRegion = useUserPreferenceStore((state) => state.selectedRegion);
  const clearFilters = useFilterStore((state) => state.clearFilters);
  const setSelectedScope = useFilterStore((state) => state.setSelectedScope);
  const setSearchText = useFilterStore((state) => state.setSearchText);
  const toggleFilter = useFilterStore((state) => state.toggleFilter);

  const activeQuery = useCourseList({
    page: 1,
    size: 100,
    statuses: ["OPEN", "DEADLINE"],
    sort: "latest",
  });
  const upcomingQuery = useCourseList({
    page: 1,
    size: 4,
    statuses: ["SCHEDULED"],
    sort: "start_date",
  });

  const isInitialLoading =
    activeQuery.isLoading && upcomingQuery.isLoading;
  const isRefreshing =
    activeQuery.isRefetching || upcomingQuery.isRefetching;
  const allFailed = activeQuery.isError && upcomingQuery.isError;
  const popularCourses = (activeQuery.data?.items ?? []).filter((course) => {
    const status = getCourseApplicationState(course).status;
    return status === "OPEN" || status === "DEADLINE";
  }).slice(0, 8);
  const deadlineCourses = (activeQuery.data?.items ?? []).filter((course) => {
    const status = getCourseApplicationState(course).status;
    return (status === "OPEN" || status === "DEADLINE") && course.apply_end !== null;
  }).sort((left, right) => (left.apply_end ?? "").localeCompare(right.apply_end ?? "")).slice(0, 5);

  function openSearch(scope?: CourseScope, onlyAvailable = false) {
    clearFilters();
    setSelectedScope(scope ?? "all");
    if (onlyAvailable) toggleFilter("OPEN");
    router.push("/search");
  }

  function openAudience(filter: ProgramFilter) {
    clearFilters();
    toggleFilter(filter);
    router.push("/search");
  }

  function openTextSearch() {
    clearFilters();
    setSearchText("");
    router.push("/search");
  }

  function refreshAll() {
    void Promise.all([
      activeQuery.refetch(),
      upcomingQuery.refetch(),
    ]);
  }

  return (
    <ScrollView
      contentInsetAdjustmentBehavior="never"
      refreshControl={
        <RefreshControl
          colors={[theme.colors.primary]}
          onRefresh={refreshAll}
          refreshing={isRefreshing && !isInitialLoading}
          tintColor={theme.colors.primary}
        />
      }
      style={styles.screen}
      contentContainerStyle={styles.content}
    >
      <AppHeader eyebrow={selectedRegion} title="문센" />

      <View style={styles.hero}>
        <View style={styles.heroCopy}>
          <Text style={styles.heroKicker}>우리 동네 배움과 즐거움</Text>
          <Text style={styles.heroTitle}>오늘 갈 곳과{`\n`}배울 것을 한 번에</Text>
          <Text style={styles.heroDescription}>문화센터부터 체험·공공교육까지 찾아보세요.</Text>
        </View>
        <Image
          accessibilityIgnoresInvertColors
          accessibilityLabel="문센 가족 로고"
          resizeMode="contain"
          source={require("../../../assets/app-icon-v2.png")}
          style={styles.heroLogo}
        />
        <Pressable
          accessibilityHint="강좌 찾기 탭으로 이동합니다"
          accessibilityLabel="강좌명, 기관명, 지역 검색"
          accessibilityRole="button"
          onPress={openTextSearch}
          style={({ pressed }) => [styles.searchEntry, pressed && styles.pressed]}
        >
          <Search color={theme.colors.primaryStrong} size={20} strokeWidth={2.4} />
          <Text numberOfLines={1} style={styles.searchText}>
            강좌명, 기관명, 지역을 검색해보세요
          </Text>
        </Pressable>
      </View>

      <View accessibilityLabel="서비스 분야 바로가기" style={styles.scopeGrid}>
        {scopeCards.map(({ scope, title, description, icon: Icon, color, backgroundColor }) => (
          <Pressable
            key={scope}
            accessibilityLabel={`${title}, ${description}`}
            accessibilityRole="button"
            onPress={() => openSearch(scope)}
            style={({ pressed }) => [styles.scopeCard, pressed && styles.pressed]}
          >
            <View style={[styles.scopeIcon, { backgroundColor }]}>
              <Icon color={color} size={22} strokeWidth={2.2} />
            </View>
            <Text style={styles.scopeTitle}>{title}</Text>
            <Text numberOfLines={1} style={styles.scopeDescription}>{description}</Text>
          </Pressable>
        ))}
      </View>

      <Pressable
        accessibilityLabel="현재 위치에서 주변 기관 보기"
        accessibilityRole="button"
        onPress={() => router.push("/centers")}
        style={({ pressed }) => [styles.locationBanner, pressed && styles.pressed]}
      >
        <View style={styles.locationIcon}>
          <LocateFixed color={theme.colors.primaryStrong} size={23} strokeWidth={2.2} />
        </View>
        <View style={styles.locationCopy}>
          <Text style={styles.locationTitle}>내 주변에서 바로 찾기</Text>
          <Text style={styles.locationDescription}>위치를 허용하면 가까운 기관과 강좌를 보여드려요.</Text>
        </View>
        <Text style={styles.locationArrow}>›</Text>
      </Pressable>

      <View accessibilityLabel="대상별 강좌 바로가기" style={styles.audienceRow}>
        {audienceButtons.map(({ label, filter, icon: Icon }) => (
          <Pressable
            key={filter}
            accessibilityLabel={`${label} 강좌 보기`}
            accessibilityRole="button"
            onPress={() => openAudience(filter)}
            style={({ pressed }) => [styles.audienceButton, pressed && styles.pressed]}
          >
            <Icon color={theme.colors.primaryStrong} size={19} strokeWidth={2.1} />
            <Text style={styles.audienceLabel}>{label}</Text>
          </Pressable>
        ))}
      </View>

      {isInitialLoading ? (
        <ActivityIndicator
          accessibilityLabel="운영 강좌 불러오는 중"
          color={theme.colors.primary}
          style={styles.loading}
        />
      ) : allFailed ? (
        <View style={styles.stateWrap}>
          <EmptyState
            title="강좌를 불러오지 못했어요"
            description="네트워크 상태를 확인하고 다시 시도해 주세요."
            actionLabel="다시 불러오기"
            onAction={refreshAll}
          />
        </View>
      ) : (
        <>
          {popularCourses.length ? (
            <>
              <SectionHeader
                title="지금 신청할 수 있어요"
                subtitle="운영 데이터에서 새로 확인된 신청 가능한 강좌"
                actionLabel="전체보기"
                onAction={() => openSearch(undefined, true)}
              />
              <ScrollView
                accessibilityLabel="인기 강좌 목록"
                horizontal
                showsHorizontalScrollIndicator={false}
                contentContainerStyle={styles.horizontalList}
              >
                {popularCourses.map((course) => (
                  <ProgramCard key={course.id} course={course} compact />
                ))}
              </ScrollView>
            </>
          ) : null}

          {deadlineCourses.length ? (
            <>
              <SectionHeader
                title="접수가 곧 끝나요"
                subtitle="접수기간이 지난 항목은 앱에서도 자동으로 마감 처리해요."
                actionLabel="전체보기"
                onAction={() => openSearch(undefined, true)}
              />
              <View style={styles.cardList}>
                {deadlineCourses.map((course) => (
                  <ProgramCard key={course.id} course={course} />
                ))}
              </View>
            </>
          ) : null}

          {upcomingQuery.data?.items.length ? (
            <>
              <SectionHeader title="곧 접수가 시작돼요" subtitle="일정을 미리 확인해두세요." />
              <View style={styles.cardList}>
                {upcomingQuery.data.items.map((course) => (
                  <ProgramCard key={course.id} course={course} />
                ))}
              </View>
            </>
          ) : null}
        </>
      )}
    </ScrollView>
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
  hero: {
    position: "relative",
    minHeight: 224,
    overflow: "hidden",
    marginHorizontal: theme.spacing.lg,
    marginBottom: theme.spacing.md,
    borderRadius: theme.radius.lg,
    backgroundColor: theme.colors.primarySoft,
  },
  heroCopy: {
    zIndex: 2,
    width: "68%",
    gap: 5,
    padding: theme.spacing.lg,
    paddingTop: theme.spacing.xl,
  },
  heroKicker: {
    color: theme.colors.primaryStrong,
    fontSize: 12,
    fontWeight: "900",
  },
  heroTitle: {
    color: theme.colors.text,
    fontSize: 23,
    fontWeight: "900",
    lineHeight: 30,
    letterSpacing: -0.6,
  },
  heroDescription: {
    color: theme.colors.primaryStrong,
    fontSize: 11,
    lineHeight: 17,
  },
  heroLogo: {
    position: "absolute",
    top: 4,
    right: -18,
    width: 150,
    height: 150,
    opacity: 0.9,
  },
  searchEntry: {
    position: "absolute",
    right: theme.spacing.md,
    bottom: theme.spacing.md,
    left: theme.spacing.md,
    minHeight: 54,
    flexDirection: "row",
    alignItems: "center",
    gap: theme.spacing.sm,
    borderRadius: theme.radius.md,
    borderWidth: 1,
    borderColor: "#B7E8DF",
    backgroundColor: "rgba(255,255,255,0.97)",
    paddingHorizontal: theme.spacing.md,
    ...theme.shadow,
  },
  searchText: {
    flex: 1,
    color: theme.colors.textMuted,
    fontSize: 13,
  },
  scopeGrid: {
    flexDirection: "row",
    gap: theme.spacing.sm,
    paddingHorizontal: theme.spacing.lg,
    paddingVertical: theme.spacing.md,
  },
  scopeCard: {
    flex: 1,
    minWidth: 0,
    minHeight: 116,
    gap: 5,
    borderRadius: theme.radius.md,
    borderWidth: 1,
    borderColor: theme.colors.border,
    backgroundColor: theme.colors.surface,
    padding: theme.spacing.md,
  },
  scopeIcon: {
    width: 40,
    height: 40,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: theme.radius.md,
  },
  scopeTitle: {
    color: theme.colors.text,
    fontSize: 13,
    fontWeight: "900",
  },
  scopeDescription: {
    color: theme.colors.textMuted,
    fontSize: 9,
  },
  locationBanner: {
    minHeight: 74,
    flexDirection: "row",
    alignItems: "center",
    gap: theme.spacing.md,
    marginHorizontal: theme.spacing.lg,
    marginBottom: theme.spacing.md,
    borderRadius: theme.radius.md,
    backgroundColor: theme.colors.surface,
    padding: theme.spacing.md,
    ...theme.shadow,
  },
  locationIcon: {
    width: 44,
    height: 44,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: theme.radius.md,
    backgroundColor: theme.colors.primarySoft,
  },
  locationCopy: {
    flex: 1,
    gap: 3,
  },
  locationTitle: {
    color: theme.colors.text,
    fontSize: 14,
    fontWeight: "900",
  },
  locationDescription: {
    color: theme.colors.textMuted,
    fontSize: 10,
    lineHeight: 15,
  },
  locationArrow: {
    color: theme.colors.primaryStrong,
    fontSize: 27,
    lineHeight: 28,
  },
  audienceRow: {
    flexDirection: "row",
    gap: theme.spacing.sm,
    paddingHorizontal: theme.spacing.lg,
    paddingBottom: theme.spacing.sm,
  },
  audienceButton: {
    flex: 1,
    minHeight: 52,
    alignItems: "center",
    justifyContent: "center",
    gap: 3,
    borderRadius: theme.radius.md,
    borderWidth: 1,
    borderColor: theme.colors.border,
    backgroundColor: theme.colors.surface,
  },
  audienceLabel: {
    color: theme.colors.text,
    fontSize: 10,
    fontWeight: "800",
  },
  horizontalList: {
    gap: theme.spacing.md,
    paddingHorizontal: theme.spacing.lg,
    paddingBottom: theme.spacing.sm,
  },
  cardList: {
    gap: theme.spacing.md,
    paddingHorizontal: theme.spacing.lg,
  },
  loading: {
    marginTop: theme.spacing.xxl,
  },
  stateWrap: {
    paddingHorizontal: theme.spacing.lg,
    paddingTop: theme.spacing.lg,
  },
  pressed: {
    opacity: 0.68,
  },
});
