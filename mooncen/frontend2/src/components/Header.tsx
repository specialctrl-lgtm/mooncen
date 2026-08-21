import { useEffect, useRef, useState, type FormEvent } from 'react';
import {
  Bell,
  BookOpenCheck,
  Bug,
  ChevronDown,
  Heart,
  LogOut,
  Search,
  UserRound,
  type LucideIcon,
} from 'lucide-react';

type HeaderProps = {
  keyword: string;
  favoriteCount: number;
  appliedCount: number;
  notificationCount: number;
  loggedIn: boolean;
  userName?: string;
  onHomeClick: () => void;
  onKeywordChange: (keyword: string) => void;
  onSubmitSearch: () => void;
  onShowBugReport: () => void;
  onShowFavorites: () => void;
  onShowApplied: () => void;
  onShowNotifications: () => void;
  onShowAccount: () => void;
  onToggleLogin: () => void;
};

type UtilityIconName = 'heart' | 'course' | 'bell' | 'user' | 'logout';

const utilityIcons: Record<UtilityIconName, LucideIcon> = {
  heart: Heart,
  course: BookOpenCheck,
  bell: Bell,
  user: UserRound,
  logout: LogOut,
};

function UtilityIcon({ name }: { name: UtilityIconName }) {
  const Icon = utilityIcons[name];
  return <Icon className="utility-icon" size={18} strokeWidth={1.9} aria-hidden="true" />;
}

export default function Header({
  keyword,
  favoriteCount,
  appliedCount,
  notificationCount,
  loggedIn,
  userName,
  onHomeClick,
  onKeywordChange,
  onSubmitSearch,
  onShowBugReport,
  onShowFavorites,
  onShowApplied,
  onShowNotifications,
  onShowAccount,
  onToggleLogin,
}: HeaderProps) {
  const [logoReady, setLogoReady] = useState(false);
  const [userMenuOpen, setUserMenuOpen] = useState(false);
  const userMenuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!userMenuOpen) return undefined;

    const closeMenu = (event: MouseEvent) => {
      if (!userMenuRef.current?.contains(event.target as Node)) setUserMenuOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setUserMenuOpen(false);
    };

    document.addEventListener('mousedown', closeMenu);
    document.addEventListener('keydown', closeOnEscape);
    return () => {
      document.removeEventListener('mousedown', closeMenu);
      document.removeEventListener('keydown', closeOnEscape);
    };
  }, [userMenuOpen]);

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    onSubmitSearch();
  }

  const displayName = userName?.trim() || '사용자';
  const compactName = displayName.slice(0, 1).toUpperCase() || 'U';

  return (
    <header className="site-header">
      <div className="topbar header-inner">
        <button
          className={`brand brand-button ${logoReady ? 'logo-loaded' : ''}`}
          type="button"
          aria-label="mooncen 홈"
          onClick={onHomeClick}
        >
          <img
            className="brand-logo"
            src="/logo-header.png"
            alt="mooncen 문센"
            width="980"
            height="260"
            decoding="async"
            onLoad={() => setLogoReady(true)}
            onError={() => setLogoReady(false)}
          />
          <span className="brand-logo-fallback" aria-hidden="true">
            <strong>mooncen</strong>
            <small>문센</small>
          </span>
        </button>

        <div className="header-search-actions">
          <form className="search-bar" role="search" onSubmit={handleSubmit}>
            <input
              type="search"
              value={keyword}
              placeholder="강좌명, 기관명, 지역 검색"
              aria-label="강좌 검색"
              onChange={(event) => onKeywordChange(event.target.value)}
            />
            <button type="submit" aria-label="검색">
              <Search size={20} strokeWidth={2} aria-hidden="true" />
            </button>
          </form>
          <button
            className="bug-report-trigger"
            type="button"
            aria-label="버그 제보"
            onClick={onShowBugReport}
          >
            <Bug size={17} strokeWidth={2} aria-hidden="true" />
            <span className="bug-report-label-full">버그 제보</span>
            <span className="bug-report-label-compact" aria-hidden="true">제보</span>
          </button>
        </div>

        <nav className="utility-nav" aria-label="사용자 메뉴">
          <button
            type="button"
            aria-label={`찜 목록${favoriteCount > 0 ? ` ${favoriteCount}개` : ''}`}
            onClick={onShowFavorites}
          >
            <UtilityIcon name="heart" />
            <span>찜</span>
            {favoriteCount > 0 && <b>{favoriteCount}</b>}
          </button>
          <button
            type="button"
            aria-label={`내 강좌${appliedCount > 0 ? ` ${appliedCount}개` : ''}`}
            onClick={onShowApplied}
          >
            <UtilityIcon name="course" />
            <span>내강좌</span>
            {appliedCount > 0 && <b>{appliedCount}</b>}
          </button>
          <button className="notification-button" type="button" aria-label="알림 보기" onClick={onShowNotifications}>
            <UtilityIcon name="bell" />
            <span>알림</span>
            {notificationCount > 0 && <span className="notification-badge">{notificationCount}</span>}
          </button>
          {loggedIn ? (
            <div className="header-user-menu" ref={userMenuRef}>
              <button
                className="user-session-badge"
                type="button"
                title={displayName}
                aria-haspopup="menu"
                aria-expanded={userMenuOpen}
                onClick={() => setUserMenuOpen((open) => !open)}
              >
                <span className="user-session-avatar" aria-hidden="true">{compactName}</span>
                <span className="user-session-full">{displayName}</span>
                <ChevronDown size={15} strokeWidth={2} aria-hidden="true" />
              </button>
              {userMenuOpen && (
                <div className="header-user-dropdown" role="menu">
                  <button
                    type="button"
                    role="menuitem"
                    onClick={() => {
                      setUserMenuOpen(false);
                      onShowAccount();
                    }}
                  >
                    <UtilityIcon name="user" />
                    계정 정보
                  </button>
                  <button
                    type="button"
                    role="menuitem"
                    onClick={() => {
                      setUserMenuOpen(false);
                      onToggleLogin();
                    }}
                  >
                    <UtilityIcon name="logout" />
                    로그아웃
                  </button>
                </div>
              )}
            </div>
          ) : (
            <button type="button" aria-label="로그인" onClick={onToggleLogin}>
              <UtilityIcon name="user" />
              <span>로그인</span>
            </button>
          )}
        </nav>
      </div>
    </header>
  );
}
