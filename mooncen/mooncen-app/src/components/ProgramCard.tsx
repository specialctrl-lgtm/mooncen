import { router } from "expo-router";
import { CalendarDays, Heart, MapPin } from "lucide-react-native";
import { useState } from "react";
import { Image, Pressable, StyleSheet, Text, View } from "react-native";

import type { CourseDto } from "../api/mooncenApi";
import { theme } from "../constants/theme";
import { useFavoriteStore } from "../stores/favoriteStore";
import {
  formatCourseDateRange,
  formatCourseFee,
  getCourseDomainLabel,
  getCourseImageUrl,
  getCourseProvider,
  getCourseSchedule,
  getCourseStatusMeta,
  getCourseVenue,
} from "../utils/coursePresentation";

type ProgramCardProps = {
  course: CourseDto;
  compact?: boolean;
  distanceLabel?: string;
};

function CourseImage({ course, compact }: { course: CourseDto; compact: boolean }) {
  const imageUrl = getCourseImageUrl(course);
  const [failedUrl, setFailedUrl] = useState<string | null>(null);

  if (!imageUrl || failedUrl === imageUrl) {
    return (
      <View style={[styles.image, compact && styles.compactImage, styles.imageFallback]}>
        <Text style={styles.imageFallbackEyebrow}>mooncen</Text>
        <Text style={styles.imageFallbackText}>{getCourseDomainLabel(course)}</Text>
      </View>
    );
  }

  return (
    <Image
      accessibilityIgnoresInvertColors
      accessibilityLabel={`${course.title} 대표 이미지`}
      onError={() => setFailedUrl(imageUrl)}
      resizeMode="cover"
      source={{ uri: imageUrl }}
      style={[styles.image, compact && styles.compactImage]}
    />
  );
}

export function ProgramCard({ course, compact = false, distanceLabel }: ProgramCardProps) {
  const favorite = useFavoriteStore((state) => state.isFavorite(course.id));
  const toggleFavorite = useFavoriteStore((state) => state.toggleFavorite);
  const status = getCourseStatusMeta(course);
  const applicationPeriod = formatCourseDateRange(course.apply_start, course.apply_end);

  return (
    <View style={[styles.card, compact && styles.compactCard]}>
      <Pressable
        accessibilityLabel={`${course.title}, ${status.label}, 상세 보기`}
        accessibilityRole="button"
        onPress={() => router.push({ pathname: "/program/[id]", params: { id: course.id } })}
        testID={`course-card-${course.id}`}
        style={({ pressed }) => [styles.body, pressed && styles.pressed]}
      >
        <CourseImage course={course} compact={compact} />
        <View style={styles.content}>
          <View style={styles.badgeRow}>
            <Text style={styles.domainBadge}>{getCourseDomainLabel(course)}</Text>
            <Text
              style={[
                styles.statusBadge,
                { color: status.color, backgroundColor: status.backgroundColor },
              ]}
            >
              {status.label}
            </Text>
          </View>

          <Text numberOfLines={2} style={styles.title}>
            {course.title}
          </Text>
          <Text numberOfLines={1} style={styles.provider}>
            {getCourseProvider(course)}
          </Text>

          <View style={styles.metaRow}>
            <MapPin color={theme.colors.textSoft} size={13} strokeWidth={2} />
            <Text numberOfLines={1} style={styles.metaText}>
              {getCourseVenue(course)}{distanceLabel ? ` · ${distanceLabel}` : ""}
            </Text>
          </View>
          <View style={styles.metaRow}>
            <CalendarDays color={theme.colors.textSoft} size={13} strokeWidth={2} />
            <Text numberOfLines={1} style={styles.metaText}>
              {getCourseSchedule(course)}
            </Text>
          </View>

          <View style={styles.bottomRow}>
            <Text numberOfLines={1} style={styles.period}>
              {applicationPeriod ? `접수 ${applicationPeriod}` : "접수기간 확인"}
            </Text>
            <Text style={styles.price}>{formatCourseFee(course.fee)}</Text>
          </View>
        </View>
      </Pressable>

      <Pressable
        accessibilityLabel={`${course.title} ${favorite ? "찜 해제" : "찜하기"}`}
        accessibilityRole="button"
        accessibilityState={{ selected: favorite }}
        hitSlop={8}
        onPress={() => toggleFavorite(course.id)}
        style={({ pressed }) => [
          styles.favoriteButton,
          favorite && styles.favoriteButtonSelected,
          pressed && styles.favoritePressed,
        ]}
      >
        <Heart
          color={favorite ? theme.colors.accent : theme.colors.textMuted}
          fill={favorite ? theme.colors.accent : "transparent"}
          size={20}
          strokeWidth={2.2}
        />
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    position: "relative",
    minHeight: 172,
    overflow: "hidden",
    borderRadius: theme.radius.md,
    borderWidth: 1,
    borderColor: theme.colors.border,
    backgroundColor: theme.colors.surface,
    ...theme.shadow,
  },
  compactCard: {
    width: 320,
  },
  body: {
    minHeight: 172,
    flexDirection: "row",
  },
  pressed: {
    opacity: 0.78,
  },
  image: {
    width: 108,
    minHeight: 172,
    backgroundColor: theme.colors.primarySoft,
  },
  compactImage: {
    width: 104,
  },
  imageFallback: {
    alignItems: "center",
    justifyContent: "center",
    gap: 3,
    padding: theme.spacing.sm,
  },
  imageFallbackEyebrow: {
    color: theme.colors.primaryStrong,
    fontSize: 12,
    fontWeight: "900",
    letterSpacing: 0.6,
  },
  imageFallbackText: {
    color: theme.colors.primaryStrong,
    fontSize: 11,
    fontWeight: "800",
    textAlign: "center",
  },
  content: {
    flex: 1,
    gap: 4,
    padding: theme.spacing.md,
    paddingRight: 44,
  },
  badgeRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: theme.spacing.xs,
  },
  domainBadge: {
    overflow: "hidden",
    borderRadius: theme.radius.pill,
    backgroundColor: theme.colors.primarySoft,
    color: theme.colors.primaryStrong,
    fontSize: 10,
    fontWeight: "800",
    paddingHorizontal: 7,
    paddingVertical: 3,
  },
  statusBadge: {
    overflow: "hidden",
    borderRadius: theme.radius.pill,
    fontSize: 10,
    fontWeight: "900",
    paddingHorizontal: 7,
    paddingVertical: 3,
  },
  title: {
    color: theme.colors.text,
    fontSize: 15,
    fontWeight: "900",
    lineHeight: 20,
    letterSpacing: -0.25,
  },
  provider: {
    color: theme.colors.primaryStrong,
    fontSize: 11,
    fontWeight: "800",
  },
  metaRow: {
    minWidth: 0,
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
  },
  metaText: {
    flex: 1,
    color: theme.colors.textMuted,
    fontSize: 10,
    lineHeight: 15,
  },
  bottomRow: {
    marginTop: "auto",
    flexDirection: "row",
    alignItems: "flex-end",
    justifyContent: "space-between",
    gap: theme.spacing.sm,
  },
  period: {
    flex: 1,
    color: theme.colors.textMuted,
    fontSize: 9,
  },
  price: {
    color: theme.colors.text,
    fontSize: 12,
    fontWeight: "900",
  },
  favoriteButton: {
    position: "absolute",
    top: theme.spacing.sm,
    right: theme.spacing.sm,
    width: 36,
    height: 36,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: theme.radius.pill,
    borderWidth: 1,
    borderColor: theme.colors.border,
    backgroundColor: "rgba(255,255,255,0.96)",
  },
  favoriteButtonSelected: {
    borderColor: theme.colors.accent,
    backgroundColor: theme.colors.accentSoft,
  },
  favoritePressed: {
    opacity: 0.62,
  },
});
