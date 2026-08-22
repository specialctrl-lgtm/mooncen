import { ExternalLink, MapPinned } from "lucide-react-native";
import { Pressable, StyleSheet, Text, View } from "react-native";

import { theme } from "../constants/theme";
import type { KakaoMapViewProps } from "./KakaoMapView.types";

export type { KakaoMapCenter, KakaoMapViewProps } from "./KakaoMapView.types";

export function KakaoMapView({
  branches,
  height = 300,
  onOpenExternal,
}: KakaoMapViewProps) {
  return (
    <View style={[styles.container, { height }]}>
      <View style={styles.iconWrap}>
        <MapPinned color={theme.colors.primaryStrong} size={30} strokeWidth={2} />
      </View>
      <Text style={styles.title}>카카오 지도는 Android·iPhone 앱에서 표시돼요.</Text>
      <Text style={styles.description}>
        웹 미리보기에서는 좌표가 확인된 {branches.length.toLocaleString("ko-KR")}개 기관 목록을 이용해 주세요.
      </Text>
      <Pressable
        accessibilityLabel="카카오맵 웹에서 열기"
        accessibilityRole="link"
        onPress={onOpenExternal}
        style={({ pressed }) => [styles.button, pressed && styles.pressed]}
      >
        <Text style={styles.buttonText}>카카오맵에서 열기</Text>
        <ExternalLink color={theme.colors.primaryStrong} size={16} />
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    alignItems: "center",
    justifyContent: "center",
    gap: theme.spacing.sm,
    borderRadius: theme.radius.md,
    borderWidth: 1,
    borderColor: theme.colors.border,
    backgroundColor: theme.colors.map,
    padding: theme.spacing.xl,
  },
  iconWrap: {
    width: 54,
    height: 54,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: theme.radius.pill,
    backgroundColor: theme.colors.primarySoft,
  },
  title: { color: theme.colors.text, fontSize: 14, fontWeight: "900", textAlign: "center" },
  description: { color: theme.colors.textMuted, fontSize: 11, lineHeight: 17, textAlign: "center" },
  button: {
    minHeight: 44,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: theme.spacing.sm,
    borderRadius: theme.radius.pill,
    backgroundColor: theme.colors.surface,
    paddingHorizontal: theme.spacing.lg,
  },
  buttonText: { color: theme.colors.primaryStrong, fontSize: 12, fontWeight: "900" },
  pressed: { opacity: 0.65 },
});
