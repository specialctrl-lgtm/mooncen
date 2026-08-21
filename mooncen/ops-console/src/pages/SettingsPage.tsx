import { useQuery } from '@tanstack/react-query';
import { opsApi } from '../api';
import { DefinitionList, PageHeader, QueryState } from '../components/Ui';

export default function SettingsPage() {
  const query = useQuery({
    queryKey: ['settings'],
    queryFn: () => opsApi<Record<string, unknown>>('/settings'),
    refetchInterval: 30_000,
  });
  return (
    <>
      <PageHeader
        eyebrow="READ-ONLY CONFIGURATION"
        title="Settings"
        description="현재 인증 방식, DB 스키마, Agent 연결과 화면 갱신 주기를 조회합니다."
      />
      <QueryState loading={query.isLoading} error={query.error} />
      {query.data && <DefinitionList value={query.data} />}
    </>
  );
}
