import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { useFilterStore } from "./filterStore";

describe("filterStore", () => {
  beforeEach(() => {
    useFilterStore.setState(useFilterStore.getInitialState(), true);
  });

  afterEach(() => {
    useFilterStore.setState(useFilterStore.getInitialState(), true);
  });

  it("clearFilters removes every user-selected filter", () => {
    const actions = useFilterStore.getState();
    actions.setSelectedScope("education");
    actions.setSelectedBranch("branch-1", "테스트 지점");
    actions.setSearchText("미술");
    actions.toggleFilter("FREE");
    actions.toggleFilter("INFANT");
    actions.toggleDay("토");
    actions.toggleTimeGroup("morning");
    actions.toggleFeeGroup("under50000");
    actions.setSort("price_asc");

    useFilterStore.getState().clearFilters();

    expect(useFilterStore.getState()).toMatchObject({
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
  });
});
