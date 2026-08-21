import AsyncStorage from "@react-native-async-storage/async-storage";
import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";

import type { CourseScope } from "../api/mooncenApi";

type UserPreferenceState = {
  selectedRegion: string;
  childBirthYearMonth?: string;
  interestedScopes: CourseScope[];
  setSelectedRegion: (selectedRegion: string) => void;
  setChildBirthYearMonth: (childBirthYearMonth?: string) => void;
  toggleInterestedScope: (scope: CourseScope) => void;
};

export const useUserPreferenceStore = create<UserPreferenceState>()(
  persist(
    (set) => ({
      selectedRegion: "내 주변",
      childBirthYearMonth: undefined,
      interestedScopes: ["provider", "experience", "education"],

      setSelectedRegion: (selectedRegion) => set({ selectedRegion }),
      setChildBirthYearMonth: (childBirthYearMonth) => set({ childBirthYearMonth }),
      toggleInterestedScope: (scope) => {
        set((state) => ({
          interestedScopes: state.interestedScopes.includes(scope)
            ? state.interestedScopes.filter((item) => item !== scope)
            : [...state.interestedScopes, scope],
        }));
      },
    }),
    {
      name: "mooncen-preferences-v2",
      version: 2,
      storage: createJSONStorage(() => AsyncStorage),
      partialize: ({ selectedRegion, childBirthYearMonth, interestedScopes }) => ({
        selectedRegion,
        childBirthYearMonth,
        interestedScopes,
      }),
    },
  ),
);
