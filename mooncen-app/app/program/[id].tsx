import { useLocalSearchParams } from "expo-router";

import { ProgramDetailScreen } from "../../src/features/program/ProgramDetailScreen";

export default function ProgramDetailRoute() {
  const { id } = useLocalSearchParams<{ id?: string | string[] }>();
  const programId = Array.isArray(id) ? id[0] ?? "" : id ?? "";
  return <ProgramDetailScreen programId={programId} />;
}
