import { useQuery } from '@tanstack/react-query';
import { useEffect, useState, type FormEvent } from 'react';
import { Navigate, Route, Routes } from 'react-router';
import { isOpsUnauthorized, opsApi } from './api';
import { loginOpsWithPassword } from './auth';
import Layout from './components/Layout';
import { QueryState } from './components/Ui';
import { OpsProvider } from './context';
import AgentsPage from './pages/AgentsPage';
import ContentPage from './pages/ContentPage';
import CrawlerAnalyticsPage from './pages/CrawlerAnalyticsPage';
import CrawlerImprovementsPage from './pages/CrawlerImprovementsPage';
import CrawlerReleasesPage from './pages/CrawlerReleasesPage';
import CrawlerStudioPage from './pages/CrawlerStudioPage';
import CrawlersPage from './pages/CrawlersPage';
import DashboardPage from './pages/DashboardPage';
import DeploymentsPage from './pages/DeploymentsPage';
import JobsAuditPage from './pages/JobsAuditPage';
import QualityPage from './pages/QualityPage';
import RegionCoveragePage from './pages/RegionCoveragePage';
import ServicesPage from './pages/ServicesPage';
import SettingsPage from './pages/SettingsPage';
import type { OpsSession } from './types';

export default function App() {
  const [authError, setAuthError] = useState<Error | null>(null);
  const [passwordPending, setPasswordPending] = useState(false);
  const session = useQuery({
    queryKey: ['ops-session'],
    queryFn: () => opsApi<OpsSession>('/session'),
    retry: false,
  });
  const refetchSession = session.refetch;

  useEffect(() => {
    const refreshExpiredSession = () => {
      void refetchSession();
    };
    window.addEventListener('mooncen:ops-auth-expired', refreshExpiredSession);
    return () => window.removeEventListener('mooncen:ops-auth-expired', refreshExpiredSession);
  }, [refetchSession]);

  if (session.isLoading) {
    return <div className="startup"><QueryState loading /></div>;
  }

  if (session.error && !isOpsUnauthorized(session.error)) {
    return (
      <div className="startup access-denied">
        <span className="eyebrow">OPS ADMIN</span>
        <h1>Ops Console 연결 오류</h1>
        <QueryState
          error={session.error}
          action={(
            <button className="button primary" type="button" onClick={() => void refetchSession()}>
              다시 시도
            </button>
          )}
        />
        <p>API, SSH 터널, 운영 DB 상태를 확인하세요. 인증 오류와 서비스 장애는 별도로 표시됩니다.</p>
      </div>
    );
  }

  if (!session.data) {
    const passwordLogin = async (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      const form = new FormData(event.currentTarget);
      const loginId = String(form.get('login_id') || '');
      const password = String(form.get('password') || '');
      setPasswordPending(true);
      setAuthError(null);
      try {
        await loginOpsWithPassword(loginId, password);
        const refreshed = await session.refetch();
        if (refreshed.error) throw refreshed.error;
      } catch (error: unknown) {
        setAuthError(error instanceof Error ? error : new Error('로그인하지 못했습니다.'));
      } finally {
        setPasswordPending(false);
      }
    };

    return (
      <div className="startup access-denied">
        <span className="eyebrow">OPS ADMIN</span>
        <h1>Ops Console 로그인</h1>
        <QueryState error={authError || session.error || new Error('관리자 세션이 없습니다.')} />
        <p>독립 운영 계정으로 로그인하세요.</p>
        <form className="password-login" onSubmit={(event) => void passwordLogin(event)}>
          <label>
            아이디
            <input
              name="login_id"
              type="text"
              autoComplete="username"
              defaultValue="opsadmin"
              required
            />
          </label>
          <label>
            비밀번호
            <input name="password" type="password" autoComplete="current-password" required />
          </label>
          <button className="button primary" type="submit" disabled={passwordPending}>
            {passwordPending ? '로그인 중…' : '로그인'}
          </button>
        </form>
      </div>
    );
  }

  return (
    <OpsProvider session={session.data}>
      <Routes>
        <Route path="/" element={<Layout />}>
          <Route index element={<Navigate to="dashboard" replace />} />
          <Route path="dashboard" element={<DashboardPage />} />
          <Route path="services" element={<ServicesPage />} />
          <Route path="services/:id" element={<ServicesPage />} />
          <Route path="crawler-studio" element={<CrawlerStudioPage />} />
          <Route path="crawler-studio/runs/:id" element={<CrawlerStudioPage />} />
          <Route path="crawlers" element={<CrawlersPage />} />
          <Route path="crawler-improvements" element={<CrawlerImprovementsPage />} />
          <Route path="crawlers/region-coverage" element={<RegionCoveragePage />} />
          <Route path="crawlers/runs/:id" element={<CrawlersPage />} />
          <Route path="crawler-releases" element={<CrawlerReleasesPage />} />
          <Route path="crawler-releases/rollouts/:rolloutId" element={<CrawlerReleasesPage />} />
          <Route path="crawler-releases/actions/:actionId" element={<CrawlerReleasesPage />} />
          <Route path="crawler-analytics" element={<CrawlerAnalyticsPage />} />
          <Route path="data-quality" element={<QualityPage />} />
          <Route path="data-quality/:id" element={<QualityPage />} />
          <Route path="content" element={<ContentPage />} />
          <Route path="content/:type/:id" element={<ContentPage />} />
          <Route path="deployments" element={<DeploymentsPage />} />
          <Route path="deployments/:id" element={<DeploymentsPage />} />
          <Route path="jobs" element={<JobsAuditPage />} />
          <Route path="jobs/:id" element={<JobsAuditPage />} />
          <Route path="audit" element={<JobsAuditPage view="audit" />} />
          <Route path="audit/:id" element={<JobsAuditPage view="audit" />} />
          <Route path="agents" element={<AgentsPage />} />
          <Route path="settings" element={<SettingsPage />} />
        </Route>
        <Route path="*" element={<Navigate to="/dashboard" replace />} />
      </Routes>
    </OpsProvider>
  );
}
