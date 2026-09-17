import { useQuery } from '@tanstack/react-query';
import { Clock, Database, Server, Shield } from 'lucide-react';
import { opsApi } from '../api';
import StatusBadge from '../components/StatusBadge';
import { PageHeader, QueryState } from '../components/Ui';
import { formatDate } from '../utils';

type SettingsData = {
  environment: string;
  auth: {
    mode: string;
    role: string;
    user: string;
  };
  database: {
    connected: boolean;
    schema: Record<string, boolean>;
    latest_migration?: {
      version: string;
      applied_at: string;
    } | null;
  };
  agents: {
    total: number;
    connected: number;
  };
  services: {
    total: number;
    healthy: number;
  };
  refresh_seconds: {
    dashboard: number;
    jobs: number;
    quality: number;
  };
};

export default function SettingsPage() {
  const query = useQuery({
    queryKey: ['settings'],
    queryFn: () => opsApi<SettingsData>('/settings'),
    refetchInterval: 30_000,
  });

  const data = query.data;

  return (
    <>
      <PageHeader
        eyebrow="READ-ONLY CONFIGURATION"
        title="Settings"
        description="인증 보안 정책, DB 스키마 마이그레이션 이력, 에이전트 연결 상태와 콘솔 갱신 주기를 조회합니다."
      />
      <QueryState loading={query.isLoading} error={query.error} />
      {data && (
        <div className="settings-grid">
          <section className="panel">
            <header className="section-header">
              <div className="card-title-with-icon">
                <Shield size={20} className="text-primary" />
                <div>
                  <h2>인증 및 계정 보안</h2>
                  <small>Ops 콘솔 전용 독립 계정 및 권한 정책</small>
                </div>
              </div>
            </header>
            <dl className="settings-dl">
              <div>
                <dt>실행 환경</dt>
                <dd>
                  <span className={`environment-badge env-${data.environment}`}>
                    {data.environment.toUpperCase()}
                  </span>
                </dd>
              </div>
              <div>
                <dt>인증 모드</dt>
                <dd>{data.auth.mode === 'single_account' ? '단일 관리자 전용 (Single Account Mode)' : data.auth.mode}</dd>
              </div>
              <div>
                <dt>현재 접속 계정</dt>
                <dd><strong>{data.auth.user}</strong></dd>
              </div>
              <div>
                <dt>부여된 권한</dt>
                <dd><StatusBadge status={data.auth.role === 'admin' ? 'healthy' : 'active'} /> {data.auth.role}</dd>
              </div>
            </dl>
          </section>

          <section className="panel">
            <header className="section-header">
              <div className="card-title-with-icon">
                <Database size={20} className="text-primary" />
                <div>
                  <h2>데이터베이스 & 스키마</h2>
                  <small>PostgreSQL 연결 및 마이그레이션 상태</small>
                </div>
              </div>
            </header>
            <dl className="settings-dl">
              <div>
                <dt>DB 연결 상태</dt>
                <dd>
                  <StatusBadge status={data.database.connected ? 'healthy' : 'failed'} />
                  {data.database.connected ? ' 정상 연결' : ' 연결 끊김'}
                </dd>
              </div>
              <div>
                <dt>최신 마이그레이션</dt>
                <dd className="mono-value">{data.database.latest_migration?.version || 'N/A'}</dd>
              </div>
              <div>
                <dt>마이그레이션 일시</dt>
                <dd>{data.database.latest_migration?.applied_at ? formatDate(data.database.latest_migration.applied_at) : '-'}</dd>
              </div>
              <div>
                <dt>주요 스키마 확인</dt>
                <dd>
                  {Object.entries(data.database.schema || {}).slice(0, 4).map(([table, ok]) => (
                    <span key={table} className={`schema-tag ${ok ? 'ok' : 'err'}`}>
                      {table}: {ok ? 'OK' : '누락'}
                    </span>
                  ))}
                </dd>
              </div>
            </dl>
          </section>

          <section className="panel">
            <header className="section-header">
              <div className="card-title-with-icon">
                <Server size={20} className="text-primary" />
                <div>
                  <h2>인프라 & 런타임 현황</h2>
                  <small>등록된 운영 서비스 및 백그라운드 에이전트</small>
                </div>
              </div>
            </header>
            <dl className="settings-dl">
              <div>
                <dt>운영 서비스</dt>
                <dd>총 {data.services.total}개 등록 (정상: {data.services.healthy}개)</dd>
              </div>
              <div>
                <dt>상태 보고 Agent</dt>
                <dd>총 {data.agents.total}개 등록 (활성 연결: {data.agents.connected}개)</dd>
              </div>
            </dl>
          </section>

          <section className="panel">
            <header className="section-header">
              <div className="card-title-with-icon">
                <Clock size={20} className="text-primary" />
                <div>
                  <h2>자동 갱신 주기 (폴링)</h2>
                  <small>화면별 실시간 데이터 폴링 간격</small>
                </div>
              </div>
            </header>
            <dl className="settings-dl">
              <div>
                <dt>대시보드 (Dashboard)</dt>
                <dd>{data.refresh_seconds.dashboard}초 간격 자동 갱신</dd>
              </div>
              <div>
                <dt>작업 및 감사 (Jobs & Audit)</dt>
                <dd>{data.refresh_seconds.jobs}초 간격 자동 갱신 (SSE 스트림 지원)</dd>
              </div>
              <div>
                <dt>데이터 품질 (Quality)</dt>
                <dd>{data.refresh_seconds.quality}초 간격 자동 갱신</dd>
              </div>
            </dl>
          </section>
        </div>
      )}
    </>
  );
}
