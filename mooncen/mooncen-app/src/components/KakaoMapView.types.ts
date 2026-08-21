import type { BranchDto } from "../api/mooncenApi";

export type KakaoMapCenter = {
  latitude: number;
  longitude: number;
};

export type KakaoMapViewProps = {
  branches: BranchDto[];
  center: KakaoMapCenter;
  height?: number;
  selectedBranchId?: string | null;
  onSelectBranch: (branchId: string) => void;
  onOpenExternal: () => void;
};
