import NetInfo from "@react-native-community/netinfo";
import { focusManager, onlineManager, QueryClientProvider } from "@tanstack/react-query";
import { Tabs } from "expo-router";
import { StatusBar } from "expo-status-bar";
import { Bookmark, Home, MapPinned, Search, UserRound } from "lucide-react-native";
import type { LucideIcon } from "lucide-react-native";
import { useEffect } from "react";
import { AppState, Platform, StyleSheet, View } from "react-native";
import type { ColorValue } from "react-native";
import {
  initialWindowMetrics,
  SafeAreaProvider,
} from "react-native-safe-area-context";

import { queryClient } from "../src/api/queryClient";
import { theme } from "../src/constants/theme";

type TabIconProps = {
  color: ColorValue;
  focused: boolean;
  icon: LucideIcon;
};

function TabIcon({ color, focused, icon: Icon }: TabIconProps) {
  return (
    <View
      accessibilityElementsHidden
      importantForAccessibility="no-hide-descendants"
      pointerEvents="none"
      style={[styles.tabIcon, focused && styles.tabIconFocused]}
    >
      <Icon color={color} size={21} strokeWidth={focused ? 2.6 : 2} />
    </View>
  );
}

function QueryRuntimeBridge() {
  useEffect(() => {
    return onlineManager.setEventListener((setOnline) =>
      NetInfo.addEventListener((state) => {
        setOnline(state.isConnected !== false);
      }),
    );
  }, []);

  useEffect(() => {
    if (Platform.OS === "web") return undefined;
    const subscription = AppState.addEventListener("change", (status) => {
      focusManager.setFocused(status === "active");
    });
    return () => subscription.remove();
  }, []);

  return null;
}

export default function RootLayout() {
  return (
    <QueryClientProvider client={queryClient}>
      <QueryRuntimeBridge />
      <SafeAreaProvider initialMetrics={initialWindowMetrics}>
        <View style={styles.safeArea}>
          <Tabs
            screenOptions={{
              headerShown: false,
              tabBarActiveTintColor: theme.colors.primary,
              tabBarInactiveTintColor: theme.colors.textMuted,
              tabBarHideOnKeyboard: true,
              tabBarLabelStyle: styles.tabLabel,
              tabBarStyle: styles.tabBar,
            }}
          >
            <Tabs.Screen
              name="index"
              options={{
                title: "홈",
                tabBarAccessibilityLabel: "홈 탭",
                tabBarIcon: ({ color, focused }) => (
                  <TabIcon color={color} focused={focused} icon={Home} />
                ),
              }}
            />
            <Tabs.Screen
              name="search"
              options={{
                title: "강좌찾기",
                tabBarAccessibilityLabel: "강좌 찾기 탭",
                tabBarIcon: ({ color, focused }) => (
                  <TabIcon color={color} focused={focused} icon={Search} />
                ),
              }}
            />
            <Tabs.Screen
              name="centers"
              options={{
                title: "지도",
                tabBarAccessibilityLabel: "내 주변 지도 탭",
                tabBarIcon: ({ color, focused }) => (
                  <TabIcon color={color} focused={focused} icon={MapPinned} />
                ),
              }}
            />
            <Tabs.Screen
              name="favorites"
              options={{
                title: "보관함",
                tabBarAccessibilityLabel: "보관한 강좌 탭",
                tabBarIcon: ({ color, focused }) => (
                  <TabIcon color={color} focused={focused} icon={Bookmark} />
                ),
              }}
            />
            <Tabs.Screen
              name="my"
              options={{
                title: "마이",
                tabBarAccessibilityLabel: "마이 탭",
                tabBarIcon: ({ color, focused }) => (
                  <TabIcon color={color} focused={focused} icon={UserRound} />
                ),
              }}
            />
            <Tabs.Screen
              name="program/[id]"
              options={{ href: null, tabBarStyle: { display: "none" } }}
            />
          </Tabs>
          <StatusBar style="dark" />
        </View>
      </SafeAreaProvider>
    </QueryClientProvider>
  );
}

const styles = StyleSheet.create({
  safeArea: {
    flex: 1,
    backgroundColor: theme.colors.background,
  },
  tabBar: {
    minHeight: 70,
    borderTopColor: theme.colors.border,
    backgroundColor: theme.colors.surface,
    paddingTop: 6,
    paddingBottom: 6,
    ...theme.shadow,
  },
  tabLabel: {
    fontSize: 10,
    fontWeight: "800",
  },
  tabIcon: {
    width: 34,
    height: 28,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: theme.radius.pill,
  },
  tabIconFocused: {
    backgroundColor: theme.colors.primarySoft,
  },
});
