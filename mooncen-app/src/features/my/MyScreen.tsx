import Constants from "expo-constants";
import { router } from "expo-router";
import {
  Bookmark,
  CloudCheck,
  CloudOff,
  ExternalLink,
  MapPinned,
  Search,
  ShieldCheck,
  Smartphone,
  Trash2,
} from "lucide-react-native";
import { Alert, Linking, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";

import type { CourseScope } from "../../api/mooncenApi";
import { useCourseList } from "../../api/programs";
import { AppHeader } from "../../components/AppHeader";
import { FilterChip } from "../../components/FilterChip";
import { theme } from "../../constants/theme";
import { useFavoriteStore } from "../../stores/favoriteStore";
import { useUserPreferenceStore } from "../../stores/userPreferenceStore";

const scopePreferences: Array<{ value: CourseScope; label: string }> = [
  { value: "provider", label: "문화센터" },
  { value: "experience", label: "전시·체험" },
  { value: "education", label: "평생교육" },
];

type MenuRowProps = {
  title: string;
  description: string;
  icon: typeof Search;
  onPress: () => void;
  destructive?: boolean;
};

function MenuRow({ title, description, icon: Icon, onPress, destructive = false }: MenuRowProps) {
  const color = destructive ? theme.colors.danger : theme.colors.primaryStrong;
  return (
    <Pressable
      accessibilityLabel={`${title}, ${description}`}
      accessibilityRole="button"
      onPress={onPress}
      style={({ pressed }) => [styles.menuRow, pressed && styles.pressed]}
    >
      <View style={[styles.menuIcon, destructive && styles.menuIconDanger]}>
        <Icon color={color} size={20} />
      </View>
      <View style={styles.menuCopy}>
        <Text style={[styles.menuTitle, destructive && styles.menuTitleDanger]}>{title}</Text>
        <Text style={styles.menuDescription}>{description}</Text>
      </View>
      <Text style={styles.chevron}>›</Text>
    </Pressable>
  );
}

export function MyScreen() {
  const favoriteIds = useFavoriteStore((state) => state.favoriteIds);
  const clearFavorites = useFavoriteStore((state) => state.clearFavorites);
  const selectedRegion = useUserPreferenceStore((state) => state.selectedRegion);
  const interestedScopes = useUserPreferenceStore((state) => state.interestedScopes);
  const toggleInterestedScope = useUserPreferenceStore((state) => state.toggleInterestedScope);
  const serviceQuery = useCourseList({ page: 1, size: 1 });
  const version = Constants.expoConfig?.version ?? "0.3.0";

  function confirmClearFavorites() {
    if (favoriteIds.length === 0) {
      Alert.alert("저장된 찜이 없어요");
      return;
    }
    Alert.alert(
      "찜을 모두 지울까요?",
      "이 기기에 저장한 찜 목록이 삭제됩니다.",
      [
        { text: "취소", style: "cancel" },
        { text: "모두 삭제", style: "destructive", onPress: clearFavorites },
      ],
    );
  }

  return (
    <ScrollView
      contentInsetAdjustmentBehavior="never"
      contentContainerStyle={styles.content}
      style={styles.screen}
    >
      <AppHeader title="마이" subtitle="이 기기에 저장된 관심 정보와 서비스 연결 상태를 관리해요." />

      <View style={styles.profileCard}>
        <View style={styles.avatar}>
          <Smartphone color={theme.colors.primaryStrong} size={27} />
        </View>
        <View style={styles.profileCopy}>
          <Text style={styles.profileTitle}>문센 로컬 사용자</Text>
          <Text style={styles.profileDescription}>로그인 없이 찜과 관심 설정을 이 기기에 저장합니다.</Text>
        </View>
        <View style={styles.localBadge}><Text style={styles.localBadgeText}>기기 저장</Text></View>
      </View>

      <View style={styles.metricsRow}>
        <Pressable onPress={() => router.push("/favorites")} style={({ pressed }) => [styles.metricCard, pressed && styles.pressed]}>
          <Bookmark color={theme.colors.accent} size={21} />
          <Text style={styles.metricValue}>{favoriteIds.length}</Text>
          <Text style={styles.metricLabel}>찜한 강좌</Text>
        </Pressable>
        <Pressable onPress={() => router.push("/centers")} style={({ pressed }) => [styles.metricCard, pressed && styles.pressed]}>
          <MapPinned color={theme.colors.primaryStrong} size={21} />
          <Text numberOfLines={1} style={styles.metricValueSmall}>{selectedRegion}</Text>
          <Text style={styles.metricLabel}>관심 위치</Text>
        </Pressable>
        <Pressable onPress={() => router.push("/search")} style={({ pressed }) => [styles.metricCard, pressed && styles.pressed]}>
          <Search color={theme.colors.secondary} size={21} />
          <Text style={styles.metricValue}>{interestedScopes.length}</Text>
          <Text style={styles.metricLabel}>관심 분야</Text>
        </Pressable>
      </View>

      <View style={styles.section}>
        <Text style={styles.sectionTitle}>관심 분야</Text>
        <Text style={styles.sectionDescription}>홈 추천과 빠른 탐색에 사용하는 설정입니다.</Text>
        <View style={styles.chipRow}>
          {scopePreferences.map((scope) => (
            <FilterChip
              key={scope.value}
              label={scope.label}
              selected={interestedScopes.includes(scope.value)}
              onPress={() => toggleInterestedScope(scope.value)}
            />
          ))}
        </View>
      </View>

      <View style={styles.section}>
        <Text style={styles.sectionTitle}>바로가기</Text>
        <MenuRow
          description="저장한 강좌의 현재 접수 상태 확인"
          icon={Bookmark}
          onPress={() => router.push("/favorites")}
          title="보관함"
        />
        <MenuRow
          description="카카오 지도에서 가까운 기관 찾기"
          icon={MapPinned}
          onPress={() => router.push("/centers")}
          title="내 주변 지도"
        />
        <MenuRow
          description="운영 웹사이트를 외부 브라우저에서 열기"
          icon={ExternalLink}
          onPress={() => void Linking.openURL("https://mooncen.kr")}
          title="문센 웹 열기"
        />
      </View>

      <View style={styles.statusCard}>
        <View style={styles.statusHeader}>
          {serviceQuery.isSuccess ? (
            <CloudCheck color={theme.colors.success} size={22} />
          ) : (
            <CloudOff color={theme.colors.warning} size={22} />
          )}
          <View style={styles.statusCopy}>
            <Text style={styles.statusTitle}>운영 데이터 연결</Text>
            <Text style={styles.statusDescription}>
              {serviceQuery.isSuccess
                ? `정상 · 운영 강좌 ${serviceQuery.data.total.toLocaleString("ko-KR")}개 연결`
                : serviceQuery.isLoading
                  ? "확인 중"
                  : "연결 확인 필요"}
            </Text>
          </View>
        </View>
        <View style={styles.privacyRow}>
          <ShieldCheck color={theme.colors.primaryStrong} size={18} />
          <Text style={styles.privacyText}>REST/지오코딩 키는 앱에 포함하지 않고, 위치는 주변 검색 요청에만 사용합니다.</Text>
        </View>
      </View>

      <View style={styles.section}>
        <Text style={styles.sectionTitle}>기기 데이터</Text>
        <MenuRow
          destructive
          description="이 기기에 저장된 찜 목록 전체 삭제"
          icon={Trash2}
          onPress={confirmClearFavorites}
          title="찜 데이터 지우기"
        />
      </View>

      <Text style={styles.version}>문센 앱 v{version} · Android / iPhone 공용</Text>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: theme.colors.background },
  content: { gap: theme.spacing.md, paddingBottom: theme.spacing.xxl },
  profileCard: {
    minHeight: 92,
    flexDirection: "row",
    alignItems: "center",
    gap: theme.spacing.md,
    marginHorizontal: theme.spacing.lg,
    borderRadius: theme.radius.lg,
    backgroundColor: theme.colors.surface,
    padding: theme.spacing.lg,
    ...theme.shadow,
  },
  avatar: {
    width: 52,
    height: 52,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: theme.radius.pill,
    backgroundColor: theme.colors.primarySoft,
  },
  profileCopy: { flex: 1, gap: 4 },
  profileTitle: { color: theme.colors.text, fontSize: 15, fontWeight: "900" },
  profileDescription: { color: theme.colors.textMuted, fontSize: 10, lineHeight: 15 },
  localBadge: { borderRadius: theme.radius.pill, backgroundColor: theme.colors.primary, paddingHorizontal: 9, paddingVertical: 6 },
  localBadgeText: { color: theme.colors.surface, fontSize: 9, fontWeight: "900" },
  metricsRow: { flexDirection: "row", gap: theme.spacing.sm, paddingHorizontal: theme.spacing.lg },
  metricCard: {
    flex: 1,
    minHeight: 102,
    alignItems: "center",
    justifyContent: "center",
    gap: 4,
    borderRadius: theme.radius.md,
    borderWidth: 1,
    borderColor: theme.colors.border,
    backgroundColor: theme.colors.surface,
  },
  metricValue: { color: theme.colors.text, fontSize: 17, fontWeight: "900" },
  metricValueSmall: { maxWidth: "88%", color: theme.colors.text, fontSize: 11, fontWeight: "900" },
  metricLabel: { color: theme.colors.textMuted, fontSize: 9 },
  section: {
    gap: theme.spacing.sm,
    marginHorizontal: theme.spacing.lg,
    borderRadius: theme.radius.md,
    borderWidth: 1,
    borderColor: theme.colors.border,
    backgroundColor: theme.colors.surface,
    padding: theme.spacing.lg,
  },
  sectionTitle: { color: theme.colors.text, fontSize: 16, fontWeight: "900" },
  sectionDescription: { color: theme.colors.textMuted, fontSize: 10 },
  chipRow: { flexDirection: "row", flexWrap: "wrap", gap: theme.spacing.sm },
  menuRow: {
    minHeight: 66,
    flexDirection: "row",
    alignItems: "center",
    gap: theme.spacing.md,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: theme.colors.border,
  },
  menuIcon: { width: 38, height: 38, alignItems: "center", justifyContent: "center", borderRadius: theme.radius.md, backgroundColor: theme.colors.primarySoft },
  menuIconDanger: { backgroundColor: theme.colors.accentSoft },
  menuCopy: { flex: 1, gap: 3 },
  menuTitle: { color: theme.colors.text, fontSize: 13, fontWeight: "900" },
  menuTitleDanger: { color: theme.colors.danger },
  menuDescription: { color: theme.colors.textMuted, fontSize: 10 },
  chevron: { color: theme.colors.textSoft, fontSize: 24 },
  statusCard: {
    gap: theme.spacing.md,
    marginHorizontal: theme.spacing.lg,
    borderRadius: theme.radius.md,
    backgroundColor: theme.colors.surfaceMuted,
    padding: theme.spacing.lg,
  },
  statusHeader: { flexDirection: "row", alignItems: "center", gap: theme.spacing.md },
  statusCopy: { flex: 1, gap: 3 },
  statusTitle: { color: theme.colors.text, fontSize: 13, fontWeight: "900" },
  statusDescription: { color: theme.colors.textMuted, fontSize: 10 },
  privacyRow: { flexDirection: "row", alignItems: "flex-start", gap: theme.spacing.sm },
  privacyText: { flex: 1, color: theme.colors.textMuted, fontSize: 10, lineHeight: 16 },
  version: { color: theme.colors.textSoft, fontSize: 10, textAlign: "center", paddingVertical: theme.spacing.md },
  pressed: { opacity: 0.65 },
});
