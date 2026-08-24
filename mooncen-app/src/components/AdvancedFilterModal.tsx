import { X } from "lucide-react-native";
import {
  Modal,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";

import { theme } from "../constants/theme";
import {
  type FeeGroup,
  type ProgramFilter,
  type TimeGroup,
  useFilterStore,
} from "../stores/filterStore";
import { FilterChip } from "./FilterChip";
import { PrimaryButton } from "./PrimaryButton";

const days = ["월", "화", "수", "목", "금", "토", "일"];
const timeGroups: Array<{ value: TimeGroup; label: string; description: string }> = [
  { value: "morning", label: "오전", description: "12시 이전" },
  { value: "afternoon", label: "오후", description: "12시~18시" },
  { value: "evening", label: "저녁", description: "18시 이후" },
];
const feeGroups: Array<{ value: FeeGroup; label: string }> = [
  { value: "free", label: "무료" },
  { value: "under50000", label: "5만원 이하" },
  { value: "under100000", label: "10만원 이하" },
  { value: "over100000", label: "10만원 초과" },
];
const audienceGroups: Array<{ value: ProgramFilter; label: string }> = [
  { value: "INFANT", label: "영유아" },
  { value: "CHILD", label: "어린이" },
  { value: "TEEN", label: "청소년" },
  { value: "ADULT", label: "성인" },
  { value: "SENIOR", label: "시니어" },
];

type AdvancedFilterModalProps = {
  visible: boolean;
  resultCount: number;
  onClose: () => void;
};

export function AdvancedFilterModal({ visible, resultCount, onClose }: AdvancedFilterModalProps) {
  const selectedDays = useFilterStore((state) => state.selectedDays);
  const selectedTimeGroups = useFilterStore((state) => state.selectedTimeGroups);
  const selectedFeeGroups = useFilterStore((state) => state.selectedFeeGroups);
  const selectedFilters = useFilterStore((state) => state.selectedFilters);
  const toggleDay = useFilterStore((state) => state.toggleDay);
  const toggleTimeGroup = useFilterStore((state) => state.toggleTimeGroup);
  const toggleFeeGroup = useFilterStore((state) => state.toggleFeeGroup);
  const toggleFilter = useFilterStore((state) => state.toggleFilter);
  const clearFilters = useFilterStore((state) => state.clearFilters);

  return (
    <Modal
      animationType="slide"
      onRequestClose={onClose}
      presentationStyle="pageSheet"
      visible={visible}
    >
      <SafeAreaView edges={["top", "bottom"]} style={styles.safeArea}>
        <View style={styles.header}>
          <View>
            <Text accessibilityRole="header" style={styles.title}>상세 필터</Text>
            <Text style={styles.subtitle}>요일·시간·가격·대상을 함께 선택할 수 있어요.</Text>
          </View>
          <Pressable
            accessibilityLabel="상세 필터 닫기"
            accessibilityRole="button"
            onPress={onClose}
            style={({ pressed }) => [styles.closeButton, pressed && styles.pressed]}
          >
            <X color={theme.colors.text} size={23} />
          </Pressable>
        </View>

        <ScrollView contentContainerStyle={styles.content}>
          <View style={styles.section}>
            <Text style={styles.sectionTitle}>모집 상태</Text>
            <Text style={styles.sectionDescription}>접수 가능한 강좌만 보려면 선택하세요.</Text>
            <View style={styles.chipRow}>
              <FilterChip
                label="접수중·마감임박"
                selected={selectedFilters.includes("OPEN")}
                onPress={() => toggleFilter("OPEN")}
              />
            </View>
          </View>

          <View style={styles.section}>
            <Text style={styles.sectionTitle}>요일</Text>
            <View style={styles.chipRow}>
              {days.map((day) => (
                <FilterChip
                  key={day}
                  label={day}
                  selected={selectedDays.includes(day)}
                  onPress={() => toggleDay(day)}
                />
              ))}
            </View>
          </View>

          <View style={styles.section}>
            <Text style={styles.sectionTitle}>수업 시간</Text>
            <View style={styles.optionGrid}>
              {timeGroups.map((timeGroup) => {
                const selected = selectedTimeGroups.includes(timeGroup.value);
                return (
                  <Pressable
                    key={timeGroup.value}
                    accessibilityLabel={`${timeGroup.label}, ${timeGroup.description}`}
                    accessibilityRole="checkbox"
                    accessibilityState={{ checked: selected }}
                    onPress={() => toggleTimeGroup(timeGroup.value)}
                    style={({ pressed }) => [
                      styles.optionCard,
                      selected && styles.optionCardSelected,
                      pressed && styles.pressed,
                    ]}
                  >
                    <Text style={[styles.optionTitle, selected && styles.optionTitleSelected]}>
                      {timeGroup.label}
                    </Text>
                    <Text style={styles.optionDescription}>{timeGroup.description}</Text>
                  </Pressable>
                );
              })}
            </View>
          </View>

          <View style={styles.section}>
            <Text style={styles.sectionTitle}>수강료</Text>
            <View style={styles.chipRow}>
              {feeGroups.map((feeGroup) => (
                <FilterChip
                  key={feeGroup.value}
                  label={feeGroup.label}
                  selected={selectedFeeGroups.includes(feeGroup.value)}
                  onPress={() => toggleFeeGroup(feeGroup.value)}
                />
              ))}
            </View>
          </View>

          <View style={styles.section}>
            <Text style={styles.sectionTitle}>대상</Text>
            <View style={styles.chipRow}>
              {audienceGroups.map(({ value, label }) => (
                <FilterChip
                  key={value}
                  label={label}
                  selected={selectedFilters.includes(value)}
                  onPress={() => toggleFilter(value)}
                />
              ))}
            </View>
          </View>
        </ScrollView>

        <View style={styles.bottomBar}>
          <PrimaryButton
            label="전체 초기화"
            onPress={clearFilters}
            style={styles.resetButton}
            variant="outline"
          />
          <PrimaryButton
            label={`${resultCount.toLocaleString("ko-KR")}개 결과 보기`}
            onPress={onClose}
            style={styles.applyButton}
          />
        </View>
      </SafeAreaView>
    </Modal>
  );
}

const styles = StyleSheet.create({
  safeArea: { flex: 1, backgroundColor: theme.colors.background },
  header: {
    minHeight: 76,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: theme.spacing.md,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: theme.colors.border,
    backgroundColor: theme.colors.surface,
    paddingHorizontal: theme.spacing.lg,
    paddingVertical: theme.spacing.md,
  },
  title: { color: theme.colors.text, fontSize: 22, fontWeight: "900" },
  subtitle: { color: theme.colors.textMuted, fontSize: 10, marginTop: 3 },
  closeButton: { width: 44, height: 44, alignItems: "center", justifyContent: "center", borderRadius: theme.radius.pill },
  content: { gap: theme.spacing.md, padding: theme.spacing.lg, paddingBottom: theme.spacing.xxl },
  section: {
    gap: theme.spacing.sm,
    borderRadius: theme.radius.md,
    borderWidth: 1,
    borderColor: theme.colors.border,
    backgroundColor: theme.colors.surface,
    padding: theme.spacing.lg,
  },
  sectionTitle: { color: theme.colors.text, fontSize: 16, fontWeight: "900" },
  sectionDescription: { color: theme.colors.textMuted, fontSize: 10 },
  chipRow: { flexDirection: "row", flexWrap: "wrap", gap: theme.spacing.sm },
  optionGrid: { flexDirection: "row", gap: theme.spacing.sm },
  optionCard: {
    flex: 1,
    minHeight: 68,
    alignItems: "center",
    justifyContent: "center",
    gap: 3,
    borderRadius: theme.radius.md,
    borderWidth: 1,
    borderColor: theme.colors.border,
    backgroundColor: theme.colors.surfaceRaised,
  },
  optionCardSelected: { borderColor: theme.colors.primary, backgroundColor: theme.colors.primarySoft },
  optionTitle: { color: theme.colors.text, fontSize: 13, fontWeight: "900" },
  optionTitleSelected: { color: theme.colors.primaryStrong },
  optionDescription: { color: theme.colors.textMuted, fontSize: 9 },
  bottomBar: {
    flexDirection: "row",
    gap: theme.spacing.sm,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: theme.colors.border,
    backgroundColor: theme.colors.surface,
    padding: theme.spacing.md,
    paddingHorizontal: theme.spacing.lg,
  },
  resetButton: { width: 104 },
  applyButton: { flex: 1 },
  pressed: { opacity: 0.65 },
});
