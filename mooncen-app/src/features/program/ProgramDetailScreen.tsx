import { router } from "expo-router";
import { ArrowLeft, ExternalLink, Heart, MapPinned, Share2 } from "lucide-react-native";
import { useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Image,
  Linking,
  Pressable,
  ScrollView,
  Share,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import type { CourseDto } from "../../api/mooncenApi";
import { useCourse } from "../../api/programs";
import { EmptyState } from "../../components/EmptyState";
import { PrimaryButton } from "../../components/PrimaryButton";
import { theme } from "../../constants/theme";
import { useFavoriteStore } from "../../stores/favoriteStore";
import {
  formatCourseDateRange,
  formatCourseCapacity,
  formatCourseFee,
  formatCourseHeadcount,
  formatMaterialFee,
  getCourseApplicationMethod,
  getCourseAddress,
  getCourseDomainLabel,
  getCourseImageUrl,
  getCourseProvider,
  getCourseSchedule,
  getCourseSourceUrl,
  getCourseStatusMeta,
  getCourseTarget,
  getCourseVenue,
} from "../../utils/coursePresentation";
import { getCourseApplicationState } from "../../utils/courseStatus";

type ProgramDetailScreenProps = {
  programId: string;
};

function DetailRow({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.detailRow}>
      <Text style={styles.detailLabel}>{label}</Text>
      <Text selectable style={styles.detailValue}>{value}</Text>
    </View>
  );
}

function compactText(value: string | null): string | null {
  const normalized = value?.replace(/\s+/g, " ").trim();
  return normalized || null;
}

function kakaoMapUrl(course: CourseDto): string | null {
  const name = getCourseVenue(course);
  const lat = course.branch?.lat;
  const lon = course.branch?.lon;
  if (lat !== null && lat !== undefined && lon !== null && lon !== undefined) {
    return `https://map.kakao.com/link/map/${encodeURIComponent(name)},${lat},${lon}`;
  }
  const address = getCourseAddress(course);
  return address ? `https://map.kakao.com/?q=${encodeURIComponent(address)}` : null;
}

export function ProgramDetailScreen({ programId }: ProgramDetailScreenProps) {
  const insets = useSafeAreaInsets();
  const courseQuery = useCourse(programId);
  const favorite = useFavoriteStore((state) => state.isFavorite(programId));
  const toggleFavorite = useFavoriteStore((state) => state.toggleFavorite);
  const [failedImageUrl, setFailedImageUrl] = useState<string | null>(null);

  function goBack() {
    if (router.canGoBack()) router.back();
    else router.replace("/");
  }

  async function openExternalUrl(url: string, errorTitle: string) {
    try {
      const canOpen = await Linking.canOpenURL(url);
      if (!canOpen) {
        Alert.alert(errorTitle, "연결할 수 있는 앱이나 브라우저가 없습니다.");
        return;
      }
      await Linking.openURL(url);
    } catch {
      Alert.alert(errorTitle, "잠시 후 다시 시도해 주세요.");
    }
  }

  async function shareCourse(course: CourseDto) {
    try {
      await Share.share({
        title: course.title,
        message: `${course.title}\nhttps://mooncen.kr/course/${encodeURIComponent(course.id)}`,
      });
    } catch {
      Alert.alert("공유하지 못했어요", "잠시 후 다시 시도해 주세요.");
    }
  }

  if (courseQuery.isLoading) {
    return (
      <View style={styles.centered}>
        <ActivityIndicator accessibilityLabel="강좌 상세 불러오는 중" color={theme.colors.primary} />
      </View>
    );
  }

  if (courseQuery.isError || !courseQuery.data) {
    return (
      <View
        style={[
          styles.emptyScreen,
          { paddingTop: insets.top + theme.spacing.lg, paddingBottom: insets.bottom + theme.spacing.lg },
        ]}
      >
        <EmptyState
          title={courseQuery.isError ? "강좌를 불러오지 못했어요" : "강좌를 찾을 수 없어요"}
          description="운영 데이터가 변경되었거나 네트워크 연결이 불안정할 수 있어요."
          actionLabel="다시 불러오기"
          onAction={() => void courseQuery.refetch()}
        />
      </View>
    );
  }

  const course = courseQuery.data;
  const application = getCourseApplicationState(course);
  const status = getCourseStatusMeta(course);
  const imageUrl = getCourseImageUrl(course);
  const showImage = imageUrl !== null && failedImageUrl !== imageUrl;
  const sourceUrl = getCourseSourceUrl(course);
  const mapUrl = kakaoMapUrl(course);
  const applicationPeriod = formatCourseDateRange(course.apply_start, course.apply_end);
  const operationPeriod = formatCourseDateRange(course.start_date, course.end_date);
  const description = compactText(course.description ?? course.ai_summary);
  const materialFee = formatMaterialFee(course.material_fee);
  const capacity = formatCourseCapacity(course.capacity_current, course.capacity_total);
  const capacityRemaining = formatCourseHeadcount(course.capacity_remaining);
  const waitlistTotal = formatCourseHeadcount(course.waitlist_total);
  const applicationMethod = getCourseApplicationMethod(course);

  function handlePrimaryAction() {
    if (application.canApply && application.applicationUrl) {
      void openExternalUrl(application.applicationUrl, "기관 신청 페이지를 열지 못했어요");
      return;
    }
    router.push("/search");
  }

  return (
    <View style={styles.screen}>
      <ScrollView
        contentInsetAdjustmentBehavior="never"
        style={styles.scrollView}
        contentContainerStyle={styles.content}
      >
        <View style={[styles.topBar, { paddingTop: insets.top }]}>
          <Pressable
            accessibilityLabel="이전 화면으로 돌아가기"
            accessibilityRole="button"
            onPress={goBack}
            style={({ pressed }) => [styles.iconButton, pressed && styles.pressed]}
          >
            <ArrowLeft color={theme.colors.text} size={23} strokeWidth={2.3} />
          </Pressable>
          <Text style={styles.topBarTitle}>강좌 상세</Text>
          <View style={styles.topActions}>
            <Pressable
              accessibilityLabel="강좌 공유하기"
              accessibilityRole="button"
              onPress={() => void shareCourse(course)}
              style={({ pressed }) => [styles.iconButton, pressed && styles.pressed]}
            >
              <Share2 color={theme.colors.text} size={21} />
            </Pressable>
            <Pressable
              accessibilityLabel={favorite ? "찜 해제" : "찜하기"}
              accessibilityRole="button"
              accessibilityState={{ selected: favorite }}
              onPress={() => toggleFavorite(course.id)}
              style={({ pressed }) => [styles.iconButton, pressed && styles.pressed]}
            >
              <Heart
                color={favorite ? theme.colors.accent : theme.colors.text}
                fill={favorite ? theme.colors.accent : "transparent"}
                size={22}
              />
            </Pressable>
          </View>
        </View>

        <View style={styles.heroMedia}>
          {showImage ? (
            <Image
              accessibilityLabel={`${course.title} 대표 이미지`}
              onError={() => setFailedImageUrl(imageUrl)}
              resizeMode="cover"
              source={{ uri: imageUrl }}
              style={styles.heroImage}
            />
          ) : (
            <View style={styles.heroFallback}>
              <Image
                accessibilityIgnoresInvertColors
                accessibilityLabel="문센 로고"
                source={require("../../../assets/app-icon-v2.png")}
                style={styles.heroFallbackLogo}
              />
              <Text style={styles.heroFallbackText}>{getCourseDomainLabel(course)}</Text>
            </View>
          )}
        </View>

        <View style={styles.body}>
          <View style={styles.badgeRow}>
            <Text style={styles.domainBadge}>{getCourseDomainLabel(course)}</Text>
            {course.standard_category ? (
              <Text style={styles.categoryBadge}>{course.standard_category}</Text>
            ) : null}
            <Text
              style={[
                styles.statusBadge,
                { color: status.color, backgroundColor: status.backgroundColor },
              ]}
            >
              {status.label}
            </Text>
          </View>

          <Text accessibilityRole="header" style={styles.title}>{course.title}</Text>
          <Text style={styles.provider}>{getCourseProvider(course)}</Text>
          <Text style={styles.venue}>{getCourseVenue(course)}</Text>

          {application.status === "CLOSED" && application.applicationExpired ? (
            <View accessibilityRole="alert" style={styles.expiredNotice}>
              <Text style={styles.expiredTitle}>접수기간이 종료된 강좌예요.</Text>
              <Text style={styles.expiredDescription}>
                기관 원문 상태가 늦게 갱신되어도 문센 앱은 접수 종료일을 기준으로 신청 버튼을 막습니다.
              </Text>
            </View>
          ) : null}

          <View style={styles.divider} />

          <View style={styles.section}>
            <Text style={styles.sectionTitle}>강좌 정보</Text>
            <View style={styles.detailList}>
              <DetailRow label="대상" value={getCourseTarget(course)} />
              <DetailRow label="수강료" value={formatCourseFee(course.fee)} />
              {materialFee ? <DetailRow label="재료비" value={materialFee} /> : null}
              {applicationPeriod ? <DetailRow label="접수기간" value={applicationPeriod} /> : null}
              {applicationMethod ? <DetailRow label="신청방법" value={applicationMethod} /> : null}
              {operationPeriod ? <DetailRow label="운영기간" value={operationPeriod} /> : null}
              <DetailRow label="일정" value={getCourseSchedule(course)} />
              {course.instructor ? <DetailRow label="강사" value={course.instructor} /> : null}
              {course.sessions !== null && course.sessions > 0 ? (
                <DetailRow label="횟수" value={`${course.sessions}회`} />
              ) : null}
              {capacity ? <DetailRow label="신청현황" value={capacity} /> : null}
              {capacityRemaining ? <DetailRow label="잔여석" value={capacityRemaining} /> : null}
              {waitlistTotal ? <DetailRow label="대기정원" value={waitlistTotal} /> : null}
              <DetailRow label="장소" value={getCourseVenue(course)} />
              {getCourseAddress(course) ? (
                <DetailRow label="주소" value={getCourseAddress(course) as string} />
              ) : null}
            </View>
          </View>

          {mapUrl ? (
            <Pressable
              accessibilityLabel="카카오맵에서 위치 보기"
              accessibilityRole="link"
              onPress={() => void openExternalUrl(mapUrl, "카카오맵을 열지 못했어요")}
              style={({ pressed }) => [styles.mapButton, pressed && styles.pressed]}
            >
              <MapPinned color={theme.colors.primaryStrong} size={20} />
              <Text style={styles.mapButtonText}>카카오맵에서 위치 보기</Text>
              <ExternalLink color={theme.colors.primaryStrong} size={16} />
            </Pressable>
          ) : null}

          {description ? (
            <>
              <View style={styles.divider} />
              <View style={styles.section}>
                <Text style={styles.sectionTitle}>강좌 소개</Text>
                <Text selectable style={styles.description}>{description}</Text>
              </View>
            </>
          ) : null}

          {sourceUrl ? (
            <Pressable
              accessibilityLabel="기관 원문 확인"
              accessibilityRole="link"
              onPress={() => void openExternalUrl(sourceUrl, "기관 원문을 열지 못했어요")}
              style={({ pressed }) => [styles.sourceButton, pressed && styles.pressed]}
            >
              <Text style={styles.sourceButtonText}>기관 원문 확인</Text>
              <ExternalLink color={theme.colors.textMuted} size={16} />
            </Pressable>
          ) : null}
        </View>
      </ScrollView>

      <View style={[styles.bottomBar, { paddingBottom: Math.max(insets.bottom, theme.spacing.sm) }]}>
        <Pressable
          accessibilityLabel={favorite ? "찜 해제" : "찜하기"}
          accessibilityRole="button"
          accessibilityState={{ selected: favorite }}
          onPress={() => toggleFavorite(course.id)}
          style={({ pressed }) => [
            styles.favoriteButton,
            favorite && styles.favoriteButtonSelected,
            pressed && styles.pressed,
          ]}
        >
          <Heart
            color={favorite ? theme.colors.accent : theme.colors.textMuted}
            fill={favorite ? theme.colors.accent : "transparent"}
            size={21}
          />
          <Text style={[styles.favoriteLabel, favorite && styles.favoriteLabelSelected]}>찜</Text>
        </Pressable>
        <PrimaryButton
          label={
            application.canApply
              ? application.status === "WAITING"
                ? "기관에서 대기 신청"
                : "기관에서 신청"
              : "비슷한 강좌 보기"
          }
          accessibilityHint={
            application.canApply
              ? "기관의 공식 신청 페이지를 엽니다."
              : "강좌 찾기 화면으로 이동합니다."
          }
          onPress={handlePrimaryAction}
          style={styles.primaryAction}
        />
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: theme.colors.background },
  scrollView: { flex: 1 },
  content: { paddingBottom: theme.spacing.xl },
  centered: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: theme.colors.background,
  },
  emptyScreen: {
    flex: 1,
    justifyContent: "center",
    backgroundColor: theme.colors.background,
    paddingHorizontal: theme.spacing.lg,
  },
  topBar: {
    minHeight: 56,
    flexDirection: "row",
    alignItems: "flex-end",
    justifyContent: "space-between",
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: theme.colors.border,
    backgroundColor: theme.colors.surface,
    paddingHorizontal: theme.spacing.sm,
    paddingBottom: 6,
  },
  topBarTitle: {
    position: "absolute",
    right: 100,
    bottom: 19,
    left: 100,
    color: theme.colors.text,
    fontSize: 16,
    fontWeight: "900",
    textAlign: "center",
  },
  topActions: { flexDirection: "row" },
  iconButton: {
    width: 44,
    height: 44,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: theme.radius.pill,
  },
  heroMedia: { height: 260, overflow: "hidden", backgroundColor: theme.colors.primarySoft },
  heroImage: { width: "100%", height: "100%" },
  heroFallback: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    gap: theme.spacing.sm,
  },
  heroFallbackLogo: { width: 122, height: 122, borderRadius: 26 },
  heroFallbackText: { color: theme.colors.primaryStrong, fontSize: 15, fontWeight: "900" },
  body: { backgroundColor: theme.colors.surface, padding: theme.spacing.lg },
  badgeRow: { flexDirection: "row", flexWrap: "wrap", gap: theme.spacing.xs },
  domainBadge: {
    overflow: "hidden",
    borderRadius: theme.radius.pill,
    backgroundColor: theme.colors.primarySoft,
    color: theme.colors.primaryStrong,
    fontSize: 11,
    fontWeight: "900",
    paddingHorizontal: theme.spacing.sm,
    paddingVertical: 5,
  },
  categoryBadge: {
    overflow: "hidden",
    borderRadius: theme.radius.pill,
    backgroundColor: theme.colors.secondarySoft,
    color: theme.colors.secondary,
    fontSize: 11,
    fontWeight: "800",
    paddingHorizontal: theme.spacing.sm,
    paddingVertical: 5,
  },
  statusBadge: {
    overflow: "hidden",
    borderRadius: theme.radius.pill,
    fontSize: 11,
    fontWeight: "900",
    paddingHorizontal: theme.spacing.sm,
    paddingVertical: 5,
  },
  title: {
    color: theme.colors.text,
    fontSize: 25,
    fontWeight: "900",
    lineHeight: 34,
    letterSpacing: -0.5,
    paddingTop: theme.spacing.md,
  },
  provider: {
    color: theme.colors.primaryStrong,
    fontSize: 14,
    fontWeight: "900",
    paddingTop: theme.spacing.xs,
  },
  venue: { color: theme.colors.textMuted, fontSize: 13, paddingTop: 3 },
  expiredNotice: {
    gap: 3,
    marginTop: theme.spacing.lg,
    borderRadius: theme.radius.md,
    borderWidth: 1,
    borderColor: "#FECACA",
    backgroundColor: "#FFF7F7",
    padding: theme.spacing.md,
  },
  expiredTitle: { color: theme.colors.danger, fontSize: 13, fontWeight: "900" },
  expiredDescription: { color: "#7F1D1D", fontSize: 11, lineHeight: 17 },
  divider: { height: 1, backgroundColor: theme.colors.border, marginVertical: theme.spacing.xl },
  section: { gap: theme.spacing.md },
  sectionTitle: { color: theme.colors.text, fontSize: 18, fontWeight: "900" },
  detailList: { gap: 0 },
  detailRow: {
    minHeight: 48,
    flexDirection: "row",
    alignItems: "flex-start",
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: theme.colors.border,
    paddingVertical: theme.spacing.md,
  },
  detailLabel: { width: 82, color: theme.colors.textMuted, fontSize: 13, fontWeight: "700", lineHeight: 21 },
  detailValue: { flex: 1, color: theme.colors.text, fontSize: 14, fontWeight: "600", lineHeight: 21 },
  mapButton: {
    minHeight: 52,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: theme.spacing.sm,
    marginTop: theme.spacing.lg,
    borderRadius: theme.radius.md,
    borderWidth: 1,
    borderColor: "#99F6E4",
    backgroundColor: theme.colors.primarySoft,
  },
  mapButtonText: { color: theme.colors.primaryStrong, fontSize: 13, fontWeight: "900" },
  description: { color: theme.colors.text, fontSize: 14, lineHeight: 23 },
  sourceButton: {
    minHeight: 48,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: theme.spacing.sm,
    marginTop: theme.spacing.xl,
    borderRadius: theme.radius.md,
    borderWidth: 1,
    borderColor: theme.colors.border,
    backgroundColor: theme.colors.surfaceRaised,
  },
  sourceButtonText: { color: theme.colors.textMuted, fontSize: 13, fontWeight: "800" },
  bottomBar: {
    flexDirection: "row",
    alignItems: "center",
    gap: theme.spacing.sm,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: theme.colors.border,
    backgroundColor: theme.colors.surface,
    paddingHorizontal: theme.spacing.lg,
    paddingTop: theme.spacing.sm,
    ...theme.shadow,
  },
  favoriteButton: {
    width: 62,
    minHeight: 52,
    alignItems: "center",
    justifyContent: "center",
    gap: 2,
    borderRadius: theme.radius.md,
    borderWidth: 1,
    borderColor: theme.colors.border,
    backgroundColor: theme.colors.surface,
  },
  favoriteButtonSelected: { borderColor: theme.colors.accent, backgroundColor: theme.colors.accentSoft },
  favoriteLabel: { color: theme.colors.textMuted, fontSize: 10, fontWeight: "800" },
  favoriteLabelSelected: { color: theme.colors.accent },
  primaryAction: { flex: 1, minHeight: 52 },
  pressed: { opacity: 0.62 },
});
