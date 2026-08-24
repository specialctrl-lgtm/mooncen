import { useState } from 'react';
import { NavLink, Outlet } from 'react-router';
import { logoutOpsSession } from '../auth';
import { useOpsSession } from '../context';
import { QueryState } from './Ui';

const navigation = [
  { to: '/dashboard', label: 'Dashboard', short: 'DB' },
  { to: '/services', label: 'Services', short: 'SV' },
  { to: '/crawler-studio', label: 'Crawler Studio', short: 'CS' },
  { to: '/crawlers', label: 'Crawlers', short: 'CR' },
  { to: '/crawler-improvements', label: 'Crawler Improvements', short: 'CI' },
  { to: '/crawler-releases', label: 'Crawler Releases', short: 'RL' },
  { to: '/crawler-analytics', label: 'Crawler Analytics', short: 'AN' },
  { to: '/data-quality', label: 'Data Quality', short: 'DQ' },
  { to: '/content', label: 'Content', short: 'CT' },
  { to: '/deployments', label: 'Deployments', short: 'DP' },
  { to: '/jobs', label: 'Jobs & Audit', short: 'JA' },
];

export default function Layout() {
  const session = useOpsSession();
  const [open, setOpen] = useState(false);
  const [logoutPending, setLogoutPending] = useState(false);
  const [logoutError, setLogoutError] = useState<Error | null>(null);

  const logout = async () => {
    setLogoutPending(true);
    setLogoutError(null);
    try {
      await logoutOpsSession();
      window.location.assign(import.meta.env.BASE_URL);
    } catch (error: unknown) {
      setLogoutError(error instanceof Error ? error : new Error('로그아웃하지 못했습니다.'));
      setLogoutPending(false);
    }
  };

  return (
    <div className={`ops-shell ${open ? 'nav-open' : ''}`}>
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark">M</span>
          <div>
            <strong>문센 Ops</strong>
            <small>운영 제어면</small>
          </div>
        </div>
        <nav aria-label="운영 콘솔 메뉴">
          {navigation.map((item) => (
            <NavLink key={item.to} to={item.to} onClick={() => setOpen(false)}>
              <span>{item.short}</span>
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-foot">
          <span>{session.user.name}</span>
          <small>{session.role.toUpperCase()}</small>
          <button
            type="button"
            disabled={logoutPending}
            onClick={() => void logout()}
          >
            {logoutPending ? '로그아웃 중…' : '로그아웃'}
          </button>
          {logoutError && <QueryState error={logoutError} />}
        </div>
      </aside>
      <button className="sidebar-scrim" type="button" aria-label="메뉴 닫기" onClick={() => setOpen(false)} />
      <div className="main-column">
        <header className={`environment-bar environment-${session.environment}`}>
          <button type="button" className="menu-button" onClick={() => setOpen(true)} aria-label="운영 메뉴 열기">
            ☰
          </button>
          <strong>{session.environment.toUpperCase()}</strong>
          <span>실제 환경 데이터 · {session.user.email}</span>
        </header>
        <main>
          <Outlet />
        </main>
      </div>
    </div>
  );
}
