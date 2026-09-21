import { NavLink } from 'react-router';

export default function CrawlerNav() {
  return (
    <nav className="segmented" aria-label="크롤러 운영 메뉴">
      <NavLink to="/crawlers" end>
        수집 현황
      </NavLink>
      <NavLink to="/crawlers/region-coverage">
        지역별 수집
      </NavLink>
      <NavLink to="/crawler-improvements">
        개선 큐
      </NavLink>
      <NavLink to="/crawler-analytics">
        분석·통계
      </NavLink>
      <NavLink to="/crawler-releases">
        릴리즈 관리
      </NavLink>
      <NavLink to="/crawler-studio">
        스튜디오
      </NavLink>
    </nav>
  );
}
