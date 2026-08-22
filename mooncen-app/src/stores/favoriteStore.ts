import AsyncStorage from "@react-native-async-storage/async-storage";
import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";

type FavoriteState = {
  favoriteIds: string[];
  addFavorite: (programId: string) => void;
  removeFavorite: (programId: string) => void;
  toggleFavorite: (programId: string) => void;
  clearFavorites: () => void;
  isFavorite: (programId: string) => boolean;
};

export const useFavoriteStore = create<FavoriteState>()(persist((set, get) => ({
  favoriteIds: [],

  addFavorite: (programId) => {
    set((state) => {
      if (state.favoriteIds.includes(programId)) {
        return state;
      }

      return { favoriteIds: [...state.favoriteIds, programId] };
    });
  },

  removeFavorite: (programId) => {
    set((state) => ({
      favoriteIds: state.favoriteIds.filter((id) => id !== programId),
    }));
  },

  toggleFavorite: (programId) => {
    const { favoriteIds, addFavorite, removeFavorite } = get();
    if (favoriteIds.includes(programId)) {
      removeFavorite(programId);
      return;
    }

    addFavorite(programId);
  },

  clearFavorites: () => set({ favoriteIds: [] }),

  isFavorite: (programId) => get().favoriteIds.includes(programId),
}), {
  name: "mooncen-favorites-v1",
  storage: createJSONStorage(() => AsyncStorage),
  partialize: (state) => ({ favoriteIds: state.favoriteIds }),
}));
