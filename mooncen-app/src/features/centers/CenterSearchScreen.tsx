import * as Location from "expo-location";
import { router } from "expo-router";
import {
  ChevronRight,
  LocateFixed,
  MapPin,
  Navigation,
  Phone,
  Search,
} from "lucide-react-native";
import { useMemo, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  FlatList,
  Linking,
  Pressable,
  RefreshControl,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";

import type { BranchDto, CourseScope } from "../../api/mooncenApi";
import { useNearbyBranches } from "../../api/programs";
import { AppHeader } from "../../components/AppHeader";
import { CategoryTabs } from "../../components/CategoryTabs";
import { EmptyState } from "../../components/EmptyState";
import { KakaoMapView, type KakaoMapCenter } from "../../components/KakaoMapView";
import { FilterChip } from "../../components/FilterChip";
import { theme } from "../../constants/theme";
import { type ScopeFilter, useFilterStore } from "../../stores/filterStore";
import { useUserPreferenceStore } from "../../stores/userPreferenceStore";

type RadiusKm = 5 | 10 | 20 | 30;

const SEOUL_CENTER: KakaoMapCenter = { latitude: 37.5665, longitude: 126.978 };
const radiusOptions: RadiusKm[] = [5, 10, 20, 30];
const scopeOptions: Array<{ label: string; value: ScopeFilter }> = [
  { label: "전체", value: "all" },
  { label: "문화센터", value: "provider" },
  { label: "전시·체험", value: "experience" },
  { label: "평생교육", value: "education" },
];

function distanceInKilometers(origin: KakaoMapCenter, branch: BranchDto): number {
  if (branch.lat === null || branch.lon === null) return Number.POSITIVE_INFINITY;
  const radians = (value: number) => value * Math.PI / 180;
  const latitudeDelta = radians(branch.lat - origin.latitude);
  const longitudeDelta = radians(branch.lon - origin.longitude);
  const latitude1 = radians(origin.latitude);
  const latitude2 = radians(branch.lat);
  const a = Math.sin(latitudeDelta / 2) ** 2
    + Math.cos(latitude1) * Math.cos(latitude2) * Math.sin(longitudeDelta / 2) ** 2;
  return 6371 * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

function formatDistance(distanceKm: number): string {
  if (!Number.isFinite(distanceKm)) return "거리 확인 필요";
  if (distanceKm < 1) return `${Math.max(10, Math.round(distanceKm * 1000))}m`;
  return `${distanceKm.toFixed(1)}km`;
}

function scopeCourseCount(branch: BranchDto, scope: ScopeFilter): number {
  if (scope === "all") {
    const counts = branch.scope_course_counts;
    return counts
      ? counts.provider + counts.education + counts.experience
      : branch.open_course_count;
  }
  return branch.scope_course_counts?.[scope] ?? 0;
}

function branchKakaoUrl(branch: BranchDto): string {
  if (branch.lat !== null && branch.lon !== null) {
    return `https://map.kakao.com/link/map/${encodeURIComponent(branch.name)},${branch.lat},${branch.lon}`;
  }
  return `https://map.kakao.com/?q=${encodeURIComponent(branch.address || branch.name)}`;
}

type BranchCardProps = {
  branch: BranchDto;
  center: KakaoMapCenter;
  scope: ScopeFilter;
  selected: boolean;
  onSelect: () => void;
  onShowCourses: () => void;
};

function BranchCard({ branch, center, scope, selected, onSelect, onShowCourses }: BranchCardProps) {
  const distance = formatDistance(distanceInKilometers(center, branch));
  return (
    <Pressable
      accessibilityLabel={`${branch.name}, ${distance}, 접수중 ${branch.open_course_count}개`}
      accessibilityRole="button"
      onPress={onSelect}
      style={({ pressed }) => [
        styles.branchCard,
        selected && styles.branchCardSelected,
        pressed && styles.pressed,
      ]}
    >
      <View style={styles.branchTopRow}>
        <View style={styles.branchIcon}>
          <MapPin color={selected ? theme.colors.accent : theme.colors.primaryStrong} size={20} />
        </View>
        <View style={styles.branchCopy}>
          <Text numberOfLines={1} style={styles.branchName}>{branch.name}</Text>
          <Text numberOfLines={1} style={styles.branchProvider}>
            {branch.provider_label || branch.provider}
          </Text>
        </View>
        <Text style={styles.distance}>{distance}</Text>
      </View>
      {branch.address ? <Text numberOfLines={1} style={styles.address}>{branch.address}</Text> : null}
      <View style={styles.branchBottomRow}>
        <View style={styles.countGroup}>
          <Text style={styles.countValue}>{scopeCourseCount(branch, scope).toLocaleString("ko-KR")}</Text>
          <Text style={styles.countLabel}>현재 강좌</Text>
        </View>
        <View style={styles.countDivider} />
        <View style={styles.countGroup}>
          <Text style={[styles.countValue, styles.openCount]}>{branch.open_course_count.toLocaleString("ko-KR")}</Text>
          <Text style={styles.countLabel}>접수중</Text>
        </View>
        <Pressable
          accessibilityLabel={`${branch.name} 강좌 보기`}
          accessibilityRole="button"
          onPress={onShowCourses}
          style={({ pressed }) => [styles.coursesButton, pressed && styles.pressed]}
        >
          <Text style={styles.coursesButtonText}>강좌 보기</Text>
          <ChevronRight color={theme.colors.primaryStrong} size={16} />
        </Pressable>
      </View>
    </Pressable>
  );
}

export function CenterSearchScreen() {
  const [center, setCenter] = useState<KakaoMapCenter>(SEOUL_CENTER);
  const [centerLabel, setCenterLabel] = useState("서울 중심");
  const [radiusKm, setRadiusKm] = useState<RadiusKm>(10);
  const [scope, setScope] = useState<ScopeFilter>("all");
  const [searchText, setSearchText] = useState("");
  const [selectedBranchId, setSelectedBranchId] = useState<string | null>(null);
  const [locating, setLocating] = useState(false);
  const setSelectedRegion = useUserPreferenceStore((state) => state.setSelectedRegion);
  const clearFilters = useFilterStore((state) => state.clearFilters);
  const setSelectedScope = useFilterStore((state) => state.setSelectedScope);
  const setSelectedBranch = useFilterStore((state) => state.setSelectedBranch);
  const branchesQuery = useNearbyBranches({
    lat: center.latitude,
    lon: center.longitude,
    radiusKm,
    limit: 1200,
  });

  const branches = useMemo(() => {
    const keyword = searchText.trim().toLocaleLowerCase("ko-KR");
    return (branchesQuery.data ?? [])
      .filter((branch) => scopeCourseCount(branch, scope) > 0)
      .filter((branch) => {
        if (!keyword) return true;
        return [branch.name, branch.provider_label, branch.address, branch.region_sigungu]
          .filter(Boolean)
          .join(" ")
          .toLocaleLowerCase("ko-KR")
          .includes(keyword);
      })
      .sort((left, right) => {
        if (left.id === selectedBranchId) return -1;
        if (right.id === selectedBranchId) return 1;
        return distanceInKilometers(center, left) - distanceInKilometers(center, right);
      });
  }, [branchesQuery.data, center, scope, searchText, selectedBranchId]);

  async function useCurrentLocation() {
    setLocating(true);
    try {
      const permission = await Location.requestForegroundPermissionsAsync();
      if (!permission.granted) {
        Alert.alert(
          "위치 권한이 필요해요",
          "권한을 허용하지 않아도 서울 중심과 기관 검색은 계속 이용할 수 있어요.",
        );
        return;
      }
      const location = await Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.Balanced });
      setCenter({
        latitude: location.coords.latitude,
        longitude: location.coords.longitude,
      });
      setCenterLabel("현재 위치");
      setSelectedRegion("현재 위치");
      setSelectedBranchId(null);
    } catch {
      Alert.alert("현재 위치를 확인하지 못했어요", "잠시 후 다시 시도하거나 서울 중심에서 검색해 주세요.");
    } finally {
      setLocating(false);
    }
  }

  async function openExternalMap(url: string) {
    try {
      await Linking.openURL(url);
    } catch {
      Alert.alert("카카오맵을 열지 못했어요", "잠시 후 다시 시도해 주세요.");
    }
  }

  function openCenterMap() {
    void openExternalMap(
      `https://map.kakao.com/link/map/${encodeURIComponent(centerLabel)},${center.latitude},${center.longitude}`,
    );
  }

  function showBranchCourses(branch: BranchDto) {
    clearFilters();
    setSelectedBranch(branch.id, branch.name);
    setSelectedScope(scope === "all" ? "all" : scope as CourseScope);
    router.push("/search");
  }

  const listHeader = (
    <>
      <AppHeader title="내 주변 지도" subtitle="실제 카카오 지도에서 가까운 기관과 현재 강좌를 확인하세요." />
      <View style={styles.locationRow}>
        <View style={styles.locationCopy}>
          <Text style={styles.locationLabel}>검색 중심</Text>
          <Text style={styles.locationValue}>{centerLabel} · 반경 {radiusKm}km</Text>
        </View>
        <Pressable
          accessibilityLabel="현재 위치 사용"
          accessibilityRole="button"
          disabled={locating}
          onPress={() => void useCurrentLocation()}
          style={({ pressed }) => [styles.locationButton, pressed && styles.pressed]}
        >
          {locating ? (
            <ActivityIndicator color={theme.colors.primaryStrong} size="small" />
          ) : (
            <LocateFixed color={theme.colors.primaryStrong} size={18} />
          )}
          <Text style={styles.locationButtonText}>현재 위치</Text>
        </Pressable>
      </View>

      <CategoryTabs options={scopeOptions} value={scope} onChange={setScope} />

      <View style={styles.radiusRow}>
        {radiusOptions.map((radius) => (
          <FilterChip
            key={radius}
            label={`${radius}km`}
            selected={radiusKm === radius}
            onPress={() => {
              setRadiusKm(radius);
              setSelectedBranchId(null);
            }}
          />
        ))}
      </View>

      <View style={styles.mapWrap}>
        <KakaoMapView
          branches={branches}
          center={center}
          height={318}
          onOpenExternal={openCenterMap}
          onSelectBranch={setSelectedBranchId}
          selectedBranchId={selectedBranchId}
        />
      </View>

      <View style={styles.searchBox}>
        <Search color={theme.colors.primaryStrong} size={19} />
        <TextInput
          accessibilityLabel="기관 이름 또는 주소 검색"
          onChangeText={setSearchText}
          placeholder="기관명, 동네, 주소 검색"
          placeholderTextColor={theme.colors.textSoft}
          style={styles.searchInput}
          value={searchText}
        />
      </View>

      <View style={styles.resultHeader}>
        <View>
          <Text accessibilityLiveRegion="polite" style={styles.resultTitle}>
            가까운 기관 {branches.length.toLocaleString("ko-KR")}곳
          </Text>
          <Text style={styles.resultDescription}>좌표와 현재 강좌가 확인된 기관만 표시합니다.</Text>
        </View>
        <Navigation color={theme.colors.primaryStrong} size={20} />
      </View>
    </>
  );

  return (
    <FlatList
      contentContainerStyle={[styles.content, branches.length === 0 && styles.emptyContent]}
      data={branches}
      keyExtractor={(branch) => branch.id}
      ListEmptyComponent={
        branchesQuery.isLoading ? (
          <ActivityIndicator
            accessibilityLabel="주변 기관 불러오는 중"
            color={theme.colors.primary}
            style={styles.loading}
          />
        ) : branchesQuery.isError ? (
          <EmptyState
            title="주변 기관을 불러오지 못했어요"
            description="네트워크 상태를 확인하고 다시 시도해 주세요."
            actionLabel="다시 불러오기"
            onAction={() => void branchesQuery.refetch()}
          />
        ) : (
          <EmptyState
            title="조건에 맞는 기관이 없어요"
            description="검색 반경을 넓히거나 분야를 전체로 바꿔 보세요."
          />
        )
      }
      ListHeaderComponent={listHeader}
      refreshControl={
        <RefreshControl
          colors={[theme.colors.primary]}
          onRefresh={() => void branchesQuery.refetch()}
          refreshing={branchesQuery.isRefetching}
          tintColor={theme.colors.primary}
        />
      }
      renderItem={({ item }) => (
        <View style={styles.branchWrap}>
          <BranchCard
            branch={item}
            center={center}
            onSelect={() => setSelectedBranchId(item.id)}
            onShowCourses={() => showBranchCourses(item)}
            scope={scope}
            selected={selectedBranchId === item.id}
          />
          {selectedBranchId === item.id ? (
            <View style={styles.selectedActions}>
              {item.phone ? (
                <Pressable
                  accessibilityLabel={`${item.name} 전화하기`}
                  accessibilityRole="link"
                  onPress={() => void Linking.openURL(`tel:${item.phone}`)}
                  style={({ pressed }) => [styles.secondaryAction, pressed && styles.pressed]}
                >
                  <Phone color={theme.colors.primaryStrong} size={17} />
                  <Text style={styles.secondaryActionText}>전화</Text>
                </Pressable>
              ) : null}
              <Pressable
                accessibilityLabel={`${item.name} 카카오맵에서 보기`}
                accessibilityRole="link"
                onPress={() => void openExternalMap(branchKakaoUrl(item))}
                style={({ pressed }) => [styles.secondaryAction, pressed && styles.pressed]}
              >
                <Navigation color={theme.colors.primaryStrong} size={17} />
                <Text style={styles.secondaryActionText}>카카오맵</Text>
              </Pressable>
            </View>
          ) : null}
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
  locationRow: {
    minHeight: 66,
    flexDirection: "row",
    alignItems: "center",
    gap: theme.spacing.md,
    marginHorizontal: theme.spacing.lg,
    borderRadius: theme.radius.md,
    borderWidth: 1,
    borderColor: theme.colors.border,
    backgroundColor: theme.colors.surface,
    padding: theme.spacing.md,
  },
  locationCopy: { flex: 1, gap: 3 },
  locationLabel: { color: theme.colors.textMuted, fontSize: 10, fontWeight: "700" },
  locationValue: { color: theme.colors.text, fontSize: 14, fontWeight: "900" },
  locationButton: {
    minWidth: 100,
    minHeight: 44,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 5,
    borderRadius: theme.radius.pill,
    backgroundColor: theme.colors.primarySoft,
    paddingHorizontal: theme.spacing.md,
  },
  locationButtonText: { color: theme.colors.primaryStrong, fontSize: 11, fontWeight: "900" },
  radiusRow: { flexDirection: "row", gap: theme.spacing.sm, paddingHorizontal: theme.spacing.lg, paddingBottom: theme.spacing.md },
  mapWrap: { paddingHorizontal: theme.spacing.lg },
  searchBox: {
    minHeight: 50,
    flexDirection: "row",
    alignItems: "center",
    gap: theme.spacing.sm,
    marginHorizontal: theme.spacing.lg,
    marginTop: theme.spacing.md,
    borderRadius: theme.radius.md,
    borderWidth: 1,
    borderColor: theme.colors.border,
    backgroundColor: theme.colors.surface,
    paddingHorizontal: theme.spacing.md,
  },
  searchInput: { flex: 1, minHeight: 48, color: theme.colors.text, fontSize: 13, paddingVertical: 0 },
  resultHeader: {
    minHeight: 74,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: theme.spacing.lg,
  },
  resultTitle: { color: theme.colors.text, fontSize: 17, fontWeight: "900" },
  resultDescription: { color: theme.colors.textMuted, fontSize: 10, marginTop: 3 },
  branchWrap: { paddingHorizontal: theme.spacing.lg, paddingBottom: theme.spacing.md },
  branchCard: {
    gap: theme.spacing.sm,
    borderRadius: theme.radius.md,
    borderWidth: 1,
    borderColor: theme.colors.border,
    backgroundColor: theme.colors.surface,
    padding: theme.spacing.md,
    ...theme.shadow,
  },
  branchCardSelected: { borderColor: theme.colors.accent, backgroundColor: "#FFFBFB" },
  branchTopRow: { flexDirection: "row", alignItems: "center", gap: theme.spacing.sm },
  branchIcon: {
    width: 38,
    height: 38,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: theme.radius.md,
    backgroundColor: theme.colors.primarySoft,
  },
  branchCopy: { flex: 1, minWidth: 0 },
  branchName: { color: theme.colors.text, fontSize: 14, fontWeight: "900" },
  branchProvider: { color: theme.colors.textMuted, fontSize: 10, marginTop: 2 },
  distance: { color: theme.colors.primaryStrong, fontSize: 11, fontWeight: "900" },
  address: { color: theme.colors.textMuted, fontSize: 11 },
  branchBottomRow: { minHeight: 42, flexDirection: "row", alignItems: "center", gap: theme.spacing.sm },
  countGroup: { alignItems: "center", minWidth: 46 },
  countValue: { color: theme.colors.text, fontSize: 14, fontWeight: "900" },
  openCount: { color: theme.colors.success },
  countLabel: { color: theme.colors.textMuted, fontSize: 9 },
  countDivider: { width: 1, height: 28, backgroundColor: theme.colors.border },
  coursesButton: {
    minHeight: 40,
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "flex-end",
    gap: 2,
  },
  coursesButtonText: { color: theme.colors.primaryStrong, fontSize: 12, fontWeight: "900" },
  selectedActions: { flexDirection: "row", gap: theme.spacing.sm, marginTop: -4 },
  secondaryAction: {
    minHeight: 44,
    flex: 1,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: theme.spacing.sm,
    borderRadius: theme.radius.md,
    borderWidth: 1,
    borderColor: theme.colors.border,
    backgroundColor: theme.colors.surface,
  },
  secondaryActionText: { color: theme.colors.primaryStrong, fontSize: 11, fontWeight: "900" },
  loading: { marginTop: theme.spacing.xxl },
  pressed: { opacity: 0.65 },
});
