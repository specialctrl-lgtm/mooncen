import { useQuery } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import { Link } from 'react-router';
import { opsApi } from '../api';
import { PageHeader, QueryState } from '../components/Ui';
import { useOpsSession } from '../context';
import { formatDate, formatNumber } from '../utils';

type ImprovementPriority = 'P0' | 'P1' | 'P2' | 'P3';

type ImprovementReason = {
  code: string;
  label: string;
  points: number | null;
};

type RecommendedAction = {
  code: string;
  label: string;
  href: string;
};

type CrawlerImprovement = {
  provider: string;
  priority: ImprovementPriority;
  score: number | null;
  evidence_complete: boolean;
  active_course_count: number | null;
  stale_48h_count: number | null;
  stale_7d_count: number | null;
  freshness_unknown_count: number | null;
  consecutive_failures: number | null;
  last_run_status: string | null;
  last_run_at: string | null;
  last_success_at: string | null;
  quality_average_score: number | null;
  quality_bad_count: number | null;
  active_quality_issue_count: number | null;
  error_category: string | null;
  error_code: string | null;
  reasons: ImprovementReason[];
  recommended_action: RecommendedAction | null;
};

type ImprovementSources = {
  runs: boolean;
  freshness: boolean;
  quality_scores: boolean;
  quality_issues: boolean;
};

type ImprovementQueueResponse = {
  schema_version: number;
  available: boolean;
  complete: boolean;
  generated_at: string | null;
  total?: number | null;
  limit?: number | null;
  truncated?: boolean | null;
  sources?: Partial<ImprovementSources> | null;
  items?: CrawlerImprovement[] | null;
};

const PRIORITIES: ImprovementPriority[] = ['P0', 'P1', 'P2', 'P3'];
const PRIORITY_ORDER: Record<ImprovementPriority, number> = { P0: 0, P1: 1, P2: 2, P3: 3 };
const SOURCE_LABELS: Record<keyof ImprovementSources, string> = {
  runs: '실행 이력',
  freshness: '최신성',
  quality_scores: '품질 점수',
  quality_issues: '품질 이슈',
};
const ALLOWED_ACTION_ROOTS = ['/crawlers', '/data-quality', '/crawler-studio', '/content'];

function providerHref(path: string, provider: string): string {
  return `${path}?${new URLSearchParams({ provider })}`;
}

export function safeImprovementHref(href: unknown, provider: string): string {
  const fallback = providerHref('/crawlers', provider);
  if (typeof href !== 'string' || !href.startsWith('/') || href.startsWith('//')) return fallback;
  try {
    const parsed = new URL(href, 'https://ops.internal.invalid');
    if (parsed.origin !== 'https://ops.internal.invalid') return fallback;
    const allowed = ALLOWED_ACTION_ROOTS.some(
      (root) => parsed.pathname === root || parsed.pathname.startsWith(`${root}/`),
    );
    if (!allowed) return fallback;
    parsed.searchParams.set('provider', provider);
    return `${parsed.pathname}?${parsed.searchParams.toString()}${parsed.hash}`;
  } catch {
    return fallback;
  }
}

function evidenceNumber(value: number | null | undefined): string {
  return value == null || !Number.isFinite(value) ? '확인 불가' : formatNumber(value);
}

function scoreText(value: number | null | undefined): string {
  return value == null || !Number.isFinite(value) ? '확인 불가' : value.toLocaleString('ko-KR');
}

function reasonText(reason: ImprovementReason): string {
  if (typeof reason.label === 'string' && reason.label.trim()) return reason.label.trim();
  const code = typeof reason.code === 'string' ? reason.code.trim() : '';
  return code ? `알 수 없는 근거 (${code})` : '알 수 없는 개선 근거';
}

function actionLabel(action: RecommendedAction | null | undefined): string {
  return typeof action?.label === 'string' && action.label.trim()
    ? action.label.trim()
    : '실행 근거 확인';
}

function ImprovementMetric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}

export default function CrawlerImprovementsPage() {
  const session = useOpsSession();
  const [priority, setPriority] = useState<'all' | ImprovementPriority>('all');
  const [providerSearch, setProviderSearch] = useState('');
  const queue = useQuery({
    queryKey: ['crawler-improvement-queue', session.environment],
    queryFn: () => opsApi<ImprovementQueueResponse>('/crawlers/improvement-queue?limit=500'),
    refetchInterval: 60_000,
  });
  const data = queue.data;
  const rankedItems = useMemo(() => [...(data?.items || [])].sort((left, right) => {
    const priorityDelta = (PRIORITY_ORDER[left.priority] ?? 99) - (PRIORITY_ORDER[right.priority] ?? 99);
    if (priorityDelta) return priorityDelta;
    const leftScore = typeof left.score === 'number' && Number.isFinite(left.score) ? left.score : -1;
    const rightScore = typeof right.score === 'number' && Number.isFinite(right.score) ? right.score : -1;
    return rightScore - leftScore || left.provider.localeCompare(right.provider);
  }), [data?.items]);
  const normalizedSearch = providerSearch.trim().toLocaleUpperCase('ko-KR');
  const filteredItems = rankedItems.filter((item) => (
    (priority === 'all' || item.priority === priority)
    && (!normalizedSearch || item.provider.toLocaleUpperCase('ko-KR').includes(normalizedSearch))
  ));
  const missingSources = (Object.keys(SOURCE_LABELS) as Array<keyof ImprovementSources>)
    .filter((source) => data?.sources?.[source] !== true)
    .map((source) => SOURCE_LABELS[source]);
  const totalCandidates = typeof data?.total === 'number' && Number.isFinite(data.total)
    ? data.total
    : rankedItems.length;

  return (
    <>
      <PageHeader
        eyebrow="CRAWLER IMPROVEMENT QUEUE"
        title="크롤러 개선 큐"
        description="실행 실패, 데이터 최신성, 품질 점수와 활성 이슈를 함께 계산해 개선할 Provider를 우선순위순으로 보여줍니다."
        actions={(
          <>
            <Link className="button subtle" to="/crawlers">실행 운영 · 이력</Link>
            <Link className="button subtle" to="/crawler-analytics">중앙 분석</Link>
          </>
        )}
      />
      <QueryState loading={queue.isLoading} error={queue.error} />
      {data?.available === false ? (
        <div className="deploy-blockers crawler-improvement-unavailable" role="alert">
          <strong>크롤러 개선 우선순위를 계산할 근거에 연결되지 않았습니다.</strong>
          <span>표시할 수 없는 수치는 0이 아닙니다. 실행 이력·최신성·품질 데이터 연결 상태를 확인하세요.</span>
        </div>
      ) : null}
      {data?.available === true && (!data.complete || missingSources.length > 0) ? (
        <div className="data-source-banner warning crawler-improvement-partial" role="alert">
          <strong>일부 근거만 반영된 개선 큐입니다.</strong>
          <span>
            {missingSources.length ? `확인 불가: ${missingSources.join(', ')}` : 'API가 부분 근거 상태로 보고했습니다.'}
          </span>
          <small>확인 불가 값은 0으로 계산하거나 표시하지 않습니다.</small>
        </div>
      ) : null}
      {data?.available === true && data.truncated === true ? (
        <div className="data-source-banner warning crawler-improvement-truncated" role="status">
          <strong>개선 후보 일부만 표시합니다.</strong>
          <span>전체 {formatNumber(totalCandidates)}개 중 상위 {formatNumber(rankedItems.length)}개 Provider를 불러왔습니다.</span>
          <small>표시되지 않은 Provider까지 검색하려면 API 페이지네이션이 필요합니다.</small>
        </div>
      ) : null}
      {data?.available === true ? (
        <>
          <section className="panel crawler-improvement-controls" aria-label="개선 큐 필터">
            <div className="scope-tabs" aria-label="우선순위 필터">
              <button
                aria-pressed={priority === 'all'}
                className={priority === 'all' ? 'active' : ''}
                type="button"
                onClick={() => setPriority('all')}
              >
                전체
              </button>
              {PRIORITIES.map((value) => (
                <button
                  key={value}
                  aria-pressed={priority === value}
                  className={priority === value ? 'active' : ''}
                  type="button"
                  onClick={() => setPriority(value)}
                >
                  {value}
                </button>
              ))}
            </div>
            <label>
              Provider 검색
              <input
                type="search"
                value={providerSearch}
                onChange={(event) => setProviderSearch(event.target.value)}
                placeholder="예: HOMEPLUS"
              />
            </label>
            <p>
              {data.complete ? '전체 근거 기준' : '현재 확인된 근거 기준'} · {filteredItems.length.toLocaleString('ko-KR')}개 표시
              {providerSearch || priority !== 'all' ? ` / ${formatNumber(totalCandidates)}개 후보` : ` / 전체 ${formatNumber(totalCandidates)}개`}
              {data.generated_at ? ` · ${formatDate(data.generated_at)} 생성` : ''}
            </p>
          </section>
          {filteredItems.length ? (
            <ol className="crawler-improvement-list" aria-label="우선순위별 크롤러 개선 후보">
              {filteredItems.map((item, index) => {
                const recommendedHref = safeImprovementHref(item.recommended_action?.href, item.provider);
                return (
                  <li className={`crawler-improvement-card priority-${item.priority.toLowerCase()}`} key={item.provider}>
                    <header>
                      <span className="improvement-rank" aria-label={`순위 ${index + 1}`}>{index + 1}</span>
                      <span className={`improvement-priority priority-${item.priority.toLowerCase()}`}>{item.priority}</span>
                      <div>
                        <h2 data-testid="improvement-provider">{item.provider}</h2>
                        <p>
                          점수 <strong>{scoreText(item.score)}</strong>
                          {' · '}{item.evidence_complete ? '근거 완전' : '근거 일부'}
                        </p>
                      </div>
                      <Link
                        aria-label={`${item.provider}: ${actionLabel(item.recommended_action)}`}
                        className="button primary"
                        to={recommendedHref}
                      >
                        {actionLabel(item.recommended_action)}
                      </Link>
                    </header>
                    <dl className="crawler-improvement-metrics">
                      <ImprovementMetric label="활성 강좌" value={evidenceNumber(item.active_course_count)} />
                      <ImprovementMetric label="48시간 초과" value={evidenceNumber(item.stale_48h_count)} />
                      <ImprovementMetric label="7일 초과" value={evidenceNumber(item.stale_7d_count)} />
                      <ImprovementMetric label="관측시각 없음" value={evidenceNumber(item.freshness_unknown_count)} />
                      <ImprovementMetric label="연속 실패" value={evidenceNumber(item.consecutive_failures)} />
                      <ImprovementMetric label="최근 실행 결과" value={item.last_run_status || '확인 불가'} />
                      <ImprovementMetric label="최근 실행" value={item.last_run_at ? formatDate(item.last_run_at) : '-'} />
                      <ImprovementMetric label="최근 성공" value={item.last_success_at ? formatDate(item.last_success_at) : '-'} />
                      <ImprovementMetric label="평균 품질 점수" value={scoreText(item.quality_average_score)} />
                      <ImprovementMetric label="Bad 강좌" value={evidenceNumber(item.quality_bad_count)} />
                      <ImprovementMetric label="활성 품질 이슈" value={evidenceNumber(item.active_quality_issue_count)} />
                      <ImprovementMetric label="오류 분류" value={item.error_category || '확인 불가'} />
                      <ImprovementMetric label="오류 코드" value={item.error_code || '-'} />
                    </dl>
                    <section className="crawler-improvement-reasons" aria-label={`${item.provider} 점수 근거`}>
                      <h3>우선순위 근거</h3>
                      {item.reasons?.length ? (
                        <ul>
                          {item.reasons.map((reason, reasonIndex) => (
                            <li key={`${reason.code || 'unknown'}-${reasonIndex}`}>
                              <span>{reasonText(reason)}</span>
                              <strong>{reason.points == null || !Number.isFinite(reason.points) ? '-' : `+${reason.points.toLocaleString('ko-KR')}점`}</strong>
                            </li>
                          ))}
                        </ul>
                      ) : <p>세부 점수 근거를 확인할 수 없습니다.</p>}
                    </section>
                    <nav className="crawler-improvement-links" aria-label={`${item.provider} 관련 화면`}>
                      <Link aria-label={`${item.provider}: 실행·오류 근거`} to={providerHref('/crawlers', item.provider)}>실행·오류 근거</Link>
                      <Link aria-label={`${item.provider}: 품질 확인`} to={providerHref('/data-quality', item.provider)}>품질 확인</Link>
                      <Link aria-label={`${item.provider}: 수정 초안·검증`} to={providerHref('/crawler-studio', item.provider)}>수정 초안·검증</Link>
                    </nav>
                  </li>
                );
              })}
            </ol>
          ) : (
            <div className="state-panel">선택한 우선순위와 Provider 조건에 해당하는 개선 후보가 없습니다.</div>
          )}
        </>
      ) : null}
    </>
  );
}
