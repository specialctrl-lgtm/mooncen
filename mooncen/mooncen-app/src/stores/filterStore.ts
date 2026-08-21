import { create } from "zustand";

import type { CourseScope, CourseSort } from "../api/mooncenApi";

export type ScopeFilter = "all" | CourseScope;
export type ProgramFilter =
  | "OPEN"
  | "FREE"
  | "INFANT"
  | "CHILD"
  | "TEEN"
  | "ADULT"
  | "SENIOR";
export type TimeGroup = "morning" | "afternoon" | "evening";
export type FeeGroup = "free" | "under50000" | "under100000" | "over100000";

type FilterState = {
  selectedScope: ScopeFilter;
  selectedBranchId?: string;
  selectedBranchLabel?: string;
  searchText: string;
  selectedFilters: ProgramFilter[];
  selectedDays: string[];
  selectedTimeGroups: TimeGroup[];
  selectedFeeGroups: FeeGroup[];
  sort: CourseSort;
  setSelectedScope: (scope: ScopeFilter) => void;
  setSelectedBranch: (branchId?: string, branchLabel?: string) => void;
  setSearchText: (searchText: string) => void;
  setSort: (sort: CourseSort) => void;
  toggleFilter: (filter: ProgramFilter) => void;
  toggleDay: (day: string) => void;
  toggleTimeGroup: (timeGroup: TimeGroup) => void;
  toggleFeeGroup: (feeGroup: FeeGroup) => void;
  clearFilters: () => void;
};

export const useFilterStore = create<FilterState>((set) => ({
  selectedScope: "all",
  selectedBranchId: undefined,
  selectedBranchLabel: undefined,
  searchText: "",
  selectedFilters: ["OPEN"],
  selectedDays: [],
  selectedTimeGroups: [],
  selectedFeeGroups: [],
  sort: "latest",

  setSelectedScope: (selectedScope) => set({ selectedScope }),
  setSelectedBranch: (selectedBranchId, selectedBranchLabel) =>
    set({ selectedBranchId, selectedBranchLabel }),
  setSearchText: (searchText) => set({ searchText }),
  setSort: (sort) => set({ sort }),

  toggleFilter: (filter) => {
    set((state) => ({
      selectedFilters: state.selectedFilters.includes(filter)
        ? state.selectedFilters.filter((item) => item !== filter)
        : [...state.selectedFilters, filter],
    }));
  },
  toggleDay: (day) => {
    set((state) => ({
      selectedDays: state.selectedDays.includes(day)
        ? state.selectedDays.filter((item) => item !== day)
        : [...state.selectedDays, day],
    }));
  },
  toggleTimeGroup: (timeGroup) => {
    set((state) => ({
      selectedTimeGroups: state.selectedTimeGroups.includes(timeGroup)
        ? state.selectedTimeGroups.filter((item) => item !== timeGroup)
        : [...state.selectedTimeGroups, timeGroup],
    }));
  },
  toggleFeeGroup: (feeGroup) => {
    set((state) => ({
      selectedFeeGroups: state.selectedFeeGroups.includes(feeGroup)
        ? state.selectedFeeGroups.filter((item) => item !== feeGroup)
        : [...state.selectedFeeGroups, feeGroup],
    }));
  },

  clearFilters: () => {
    set({
      selectedScope: "all",
      selectedBranchId: undefined,
      selectedBranchLabel: undefined,
      searchText: "",
      selectedFilters: [],
      selectedDays: [],
      selectedTimeGroups: [],
      selectedFeeGroups: [],
      sort: "latest",
    });
  },
}));
