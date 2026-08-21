import { Pressable, StyleSheet, Text } from "react-native";

import { theme } from "../constants/theme";

type FilterChipProps = {
  label: string;
  selected?: boolean;
  onPress: () => void;
};

export function FilterChip({ label, selected = false, onPress }: FilterChipProps) {
  return (
    <Pressable
      accessibilityRole="checkbox"
      accessibilityLabel={`${label} 필터`}
      accessibilityState={{ checked: selected }}
      aria-checked={selected}
      onPress={onPress}
      style={[styles.chip, selected && styles.selected]}
    >
      <Text style={[styles.label, selected && styles.selectedLabel]}>{label}</Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  chip: {
    minHeight: 44,
    justifyContent: "center",
    borderRadius: theme.radius.pill,
    borderWidth: 1,
    borderColor: theme.colors.border,
    backgroundColor: theme.colors.surface,
    paddingHorizontal: theme.spacing.md,
  },
  selected: {
    borderColor: theme.colors.primary,
    backgroundColor: theme.colors.primary,
  },
  label: {
    color: theme.colors.textMuted,
    fontSize: 14,
    fontWeight: "700",
  },
  selectedLabel: {
    color: theme.colors.surface,
  },
});
