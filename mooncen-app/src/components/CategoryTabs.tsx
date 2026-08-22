import { Pressable, ScrollView, StyleSheet, Text } from "react-native";

import { theme } from "../constants/theme";

type CategoryTabsProps<TValue extends string> = {
  options: Array<{ label: string; value: TValue }>;
  value: TValue;
  onChange: (value: TValue) => void;
};

export function CategoryTabs<TValue extends string>({
  options,
  value,
  onChange,
}: CategoryTabsProps<TValue>) {
  return (
    <ScrollView
      accessibilityLabel="프로그램 분야"
      accessibilityRole="tablist"
      horizontal
      showsHorizontalScrollIndicator={false}
      contentContainerStyle={styles.container}
    >
      {options.map((option) => {
        const selected = option.value === value;
        return (
          <Pressable
            key={option.value}
            accessibilityRole="tab"
            accessibilityLabel={`${option.label} 분야`}
            accessibilityState={{ selected }}
            onPress={() => onChange(option.value)}
            style={[styles.tab, selected && styles.tabSelected]}
          >
            <Text style={[styles.label, selected && styles.labelSelected]}>{option.label}</Text>
          </Pressable>
        );
      })}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: {
    gap: theme.spacing.sm,
    paddingHorizontal: theme.spacing.lg,
    paddingVertical: theme.spacing.sm,
  },
  tab: {
    minHeight: 44,
    justifyContent: "center",
    borderRadius: theme.radius.pill,
    borderWidth: 1,
    borderColor: theme.colors.border,
    backgroundColor: theme.colors.surface,
    paddingHorizontal: theme.spacing.md,
  },
  tabSelected: {
    borderColor: theme.colors.primary,
    backgroundColor: theme.colors.primary,
  },
  label: {
    color: theme.colors.textMuted,
    fontSize: 15,
    fontWeight: "700",
  },
  labelSelected: {
    color: theme.colors.surface,
  },
});
