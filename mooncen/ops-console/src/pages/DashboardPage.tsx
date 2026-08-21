import { useQueries } from '@tanstack/react-query';
import type { CSSProperties } from 'react';
import { useNavigate } from 'react-router';
import { opsApi } from '../api';
import StatusBadge from '../components/StatusBadge';
import { PageHeader, QueryState, StatCard } from '../components/Ui';
import type { CollectionSummary, DashboardSummary, QualitySummary, VisitorSummary } from '../types';
import { formatDate, formatNumber } from '../utils';

type AlertsResponse = { available: boolean; items: Array<Record<string, unknown>> };
type RecentJobsResponse = { available: boolean; items: Array<Record<string, unknown>> };

const visitorUnavailableReasons: Record<string, string> = {
  cloudflare_analytics_not_configured: 'Cloudflare 방문 분석 연동이 아직 설정되지 않았습니다.',
  cloudflare_not_configured: 'Cloudflare 방문 분석 연동이 아직 설정되지 않았습니다.',
  not_configured: 'Cloudflare 방문 분석 연동이 아직 설정되지 않았습니다.',
  cloudflare_analytics_unavailable: 'Cloudflare 방문 분석 데이터를 현재 가져올 수 없습니다. 잠시 후 다시 확인해 주세요.',
  cloudflare_api_unavailable: 'Cloudflare 방문 분석 데이터를 현재 가져올 수 없습니다. 잠시 후 다시 확인해 주세요.',
  upstream_unavailable: 'Cloudflare 방문 분석 데이터를 현재 가져올 수 없습니다. 잠시 후 다시 확인해 주세요.',
  cloudflare_analytics_invalid_response: 'Cloudflare 방문 분석 응답을 확인할 수 없습니다.',
  invalid_response: 'Cloudflare 방문 분석 응답을 확인할 수 없습니다.',
  cloudflare_analytics_range_unavailable: '요청한 기간의 Cloudflare 방문 분석 범위를 조회할 수 없습니다. 잠시 후 다시 확인해 주세요.',
};

function visitorUnavailableMessage(reasonCode?: string | null) {
  const normalized = reasonCode?.trim().toLowerCase() || '';
  return visitorUnavailableReasons[normalized] || '방문 분석 데이터를 사용할 수 없습니다. Cloudflare 연동 상태를 확인해 주세요.';
}

function formatVisitorDate(value: string) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  return match ? `${Number(match[2])}.${Number(match[3])}` : value;
}

function formatVisitorDataThrough(value: string, timeZone: string) {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  try {
    return new Intl.DateTimeFormat('ko-KR', {
      timeZone,
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    }).format(parsed);
  } catch {
    return formatDate(value);
  }
}

function VisitorAnalyticsPanel({ visitor }: { visitor: Extract<VisitorSummary, { available: true }> }) {
  const periods = [
    ['오늘 방문 추정치', visitor.summary.today],
    ['어제 방문 추정치', visitor.summary.yesterday],
    ['최근 7일 방문 추정치', visitor.summary.last_7_days],
  ] as const;
  const maxVisits = Math.max(1, ...visitor.series.map((point) => point.visits));
  const confidenceLevel = Number.isFinite(visitor.sampling.confidence_level)
    ? `${Math.round(visitor.sampling.confidence_level * 100)}% 신뢰수준`
    : null;

  return (
    <>
      <div className="visitor-summary-grid">
        {periods.map(([label, period]) => (
          <StatCard
            key={label}
            label={label}
            value={`${formatNumber(period.visits)}회`}
            note={`HTTP 요청 ${formatNumber(period.requests)}회${period.partial ? ' · 집계 중' : ''}`}
          />
        ))}
      </div>
      <p className="visitor-metric-note">
        Cloudflare Adaptive Sampling 기반 추정치{confidenceLevel ? `(${confidenceLevel})` : ''}입니다.
        {visitor.sampling.aggregate_bounds_available ? '' : ' 기간 합계 오차 범위는 제공되지 않습니다.'}
        {' '}방문 추정치는 고유 사용자 수가 아니며 자동화된 트래픽이 포함될 수 있습니다. HTTP 요청은 문서·이미지·API 등을 포함한 요청 수로 페이지뷰가 아닙니다.
      </p>
      {visitor.series.length > 0 && (
        <figure className="visitor-trend" aria-labelledby="visitor-trend-title">
          <figcaption>
            <strong id="visitor-trend-title">최근 7일 일별 방문 추정치</strong>
            <span>방문 추정치</span>
          </figcaption>
          <ol className="visitor-bars">
            {visitor.series.map((point) => {
              const barHeight = `${Math.max(0, Math.min(100, (point.visits / maxVisits) * 100))}%`;
              const detail = `${formatVisitorDate(point.date)} 방문 추정 ${formatNumber(point.visits)}회, HTTP 요청 ${formatNumber(point.requests)}회${point.partial ? ', 집계 중' : ''}`;
              return (
                <li key={point.date} aria-label={detail} title={detail}>
                  <span className="visitor-bar-track" aria-hidden="true">
                    <span
                      className={`visitor-bar-fill${point.visits > 0 ? ' has-value' : ''}`}
                      style={{ '--visitor-bar-height': barHeight } as CSSProperties}
                    />
                  </span>
                  <time dateTime={point.date} aria-hidden="true">{formatVisitorDate(point.date)}</time>
                </li>
              );
            })}
          </ol>
        </figure>
      )}
    </>
  );
}

function componentHostSummary(component: DashboardSummary['components'][number]) {
  const configured = component.configured_owner_host || component.topology_host || null;
  const observed = component.observed_runtime_host || null;
  const reporter = component.reporter_hostname || null;
  const parts: string[] = [];
  if (configured) parts.push(`설정 ${configured}`);
  if (observed && observed.toLowerCase() !== configured?.toLowerCase()) {
    parts.push(`실행 ${observed}`);
  }
  if (
    reporter &&
    reporter.toLowerCase() !== configured?.toLowerCase() &&
    reporter.toLowerCase() !== observed?.toLowerCase()
  ) {
    parts.push(`보고 ${reporter}`);
  }
  if (!configured && !observed && !reporter && component.service_host) {
    parts.push(`Endpoint ${component.service_host}`);
  }
  return parts.join(' · ');
}

export default function DashboardPage() {
  const navigate = useNavigate();
  const results = useQueries({
    queries: [
      { queryKey: ['dashboard', 'summary'], queryFn: () => opsApi<DashboardSummary>('/dashboard/summary'), refetchInterval: 30_000 },
      {
        queryKey: ['dashboard', 'collection'],
        queryFn: () => opsApi<CollectionSummary>('/dashboard/collection-summary'),
        refetchInterval: 30_000,
      },
      {
        queryKey: ['dashboard', 'quality'],
        queryFn: () => opsApi<QualitySummary>('/dashboard/quality-summary'),
        refetchInterval: 60_000,
      },
      { queryKey: ['dashboard', 'alerts'], queryFn: () => opsApi<AlertsResponse>('/dashboard/alerts?limit=10'), refetchInterval: 30_000 },
      {
        queryKey: ['dashboard', 'jobs'],
        queryFn: () => opsApi<RecentJobsResponse>('/dashboard/recent-jobs?limit=8'),
        refetchInterval: 15_000,
      },
      {
        queryKey: ['dashboard', 'visitor-summary', 7],
        queryFn: () => opsApi<VisitorSummary>('/dashboard/visitor-summary?days=7'),
        staleTime: 300_000,
        refetchInterval: 300_000,
      },
    ],
  });
  const [summaryQuery, collectionQuery, qualityQuery, alertsQuery, jobsQuery, visitorQuery] = results;
  const summary = summaryQuery.data as DashboardSummary | undefined;
  const collection = collectionQuery.data as CollectionSummary | undefined;
  const quality = qualityQuery.data as QualitySummary | undefined;
  const alerts = alertsQuery.data as AlertsResponse | undefined;
  const jobs = jobsQuery.data as RecentJobsResponse | undefined;
  const visitor = visitorQuery.data as VisitorSummary | undefined;

  return (
    <>
      <PageHeader
        eyebrow="OPERATIONS OVERVIEW"
        title="Dashboard"
        description="문제 발견에서 상세 화면 이동까지 필요한 현재 상태만 요약합니다."
        actions={
          summary?.grafana_url ? (
            <a className="button subtle" href={summary.grafana_url} target="_blank" rel="noreferrer">
              Grafana 열기
            </a>
          ) : undefined
        }
      />
      <QueryState loading={summaryQuery.isLoading} error={summaryQuery.error} />
      {summary && (
        <section className="status-hero">
          <div>
            <span>전체 상태</span>
            <strong>{summary.overall_status === 'unknown' ? '연동 확인 필요' : '운영 상태'}</strong>
          </div>
          <StatusBadge status={summary.overall_status} />
          <small>마지막 계산 {formatDate(summary.generated_at)}</small>
        </section>
      )}
      <section className="component-grid">
        {summary?.components.slice(0, 6).map((component) => {
          const hostSummary = componentHostSummary(component);
          return (
            <article key={component.type} className="component-card">
              <div>
                <span>{component.name}</span>
                <StatusBadge status={component.status} />
              </div>
              <strong>{component.response_time_ms == null ? '-' : `${component.response_time_ms}ms`}</strong>
              <small>
                {hostSummary ? `${hostSummary} · ` : ''}
                {component.current_commit || component.current_version || formatDate(component.last_checked_at)}
              </small>
            </article>
          );
        })}
      </section>

      <div className="dashboard-columns">
        <section className="panel">
          <header className="section-header">
            <div>
              <span className="eyebrow">TODAY</span>
              <h2>오늘의 수집 현황</h2>
            </div>
            <button className="text-button" type="button" onClick={() => navigate('/crawlers')}>
              전체 보기 →
            </button>
          </header>
          <QueryState loading={collectionQuery.isLoading} error={collectionQuery.error} unavailable={collection?.available === false} />
          {collection?.available && (
            <div className="stats-grid">
              <StatCard label="수집" value={formatNumber(collection.today.collected)} note={formatDate(collection.last_collection_at)} />
              <StatCard label="신규" value={formatNumber(collection.today.new)} tone="good" />
              <StatCard label="변경" value={formatNumber(collection.today.updated)} />
              <StatCard label="실패 실행" value={formatNumber(collection.today.failed)} tone={collection.today.failed ? 'bad' : 'good'} />
              <StatCard
                label="삭제 후보"
                value={formatNumber(collection.today.deleted_candidates)}
                tone={collection.today.deleted_candidates ? 'warn' : 'neutral'}
              />
              <StatCard label="실행 중" value={formatNumber(collection.today.running)} />
            </div>
          )}
        </section>

        <section className="panel">
          <header className="section-header">
            <div>
              <span className="eyebrow">QUALITY</span>
              <h2>데이터 품질</h2>
            </div>
            <button className="text-button" type="button" onClick={() => navigate('/data-quality')}>
              작업대 열기 →
            </button>
          </header>
          <QueryState loading={qualityQuery.isLoading} error={qualityQuery.error} unavailable={quality?.available === false} />
          {quality?.available && (
            <div className="quality-list">
              {[
                ['필수 필드 누락', 'missing_required'],
                ['중복 URL', 'duplicate_urls'],
                ['날짜 이상', 'invalid_dates'],
                ['가격 이상', 'invalid_prices'],
                ['주소 없음', 'missing_address'],
                ['좌표 없음', 'missing_coordinates'],
                ['자동 반영 차단', 'blocked_sync'],
              ].map(([label, key]) => (
                <div key={key}>
                  <span>{label}</span>
                  <strong className={quality.counts[key] ? 'text-warn' : ''}>{formatNumber(quality.counts[key])}</strong>
                </div>
              ))}
            </div>
          )}
        </section>
      </div>

      <section className="panel visitor-panel" aria-labelledby="visitor-panel-title">
        <header className="section-header">
          <div>
            <span className="eyebrow">WEB ANALYTICS</span>
            <h2 id="visitor-panel-title">MoonCen 방문 현황</h2>
            <small>
              최근 7일 · 한국시간(KST) 기준 · 완료된 시간 단위 · 5분 간격 갱신
              {visitor?.available && visitor.data_through
                ? ` · 집계 종료 ${formatVisitorDataThrough(visitor.data_through, visitor.timezone)}`
                : ''}
            </small>
          </div>
        </header>
        <QueryState loading={visitorQuery.isLoading} error={visitorQuery.error} />
        {visitor?.available === false && (
          <div className="state-panel" role="status">{visitorUnavailableMessage(visitor.reason_code)}</div>
        )}
        {visitor?.available && <VisitorAnalyticsPanel visitor={visitor} />}
      </section>

      <div className="dashboard-columns">
        <section className="panel">
          <header className="section-header">
            <h2>최근 경고</h2>
          </header>
          <QueryState loading={alertsQuery.isLoading} error={alertsQuery.error} empty={alerts?.items.length === 0} />
          <div className="activity-list">
            {alerts?.items.map((alert) => (
              <button
                type="button"
                key={String(alert.id)}
                onClick={() =>
                  alert.resource_type === 'crawler_run' && alert.resource_id
                    ? navigate(`/crawlers/runs/${String(alert.resource_id)}`)
                    : navigate('/data-quality')
                }
              >
                <StatusBadge status={String(alert.severity)} />
                <span>
                  <strong>{String(alert.title || '-')}</strong>
                  <small>{String(alert.message || '')}</small>
                </span>
                <time>{formatDate(alert.detected_at)}</time>
              </button>
            ))}
          </div>
        </section>
        <section className="panel">
          <header className="section-header">
            <h2>최근 작업</h2>
          </header>
          <QueryState loading={jobsQuery.isLoading} error={jobsQuery.error} unavailable={jobs?.available === false} empty={jobs?.items.length === 0} />
          <div className="activity-list">
            {jobs?.items.map((job) => (
              <button type="button" key={String(job.id)} onClick={() => navigate(`/jobs/${String(job.id)}`)}>
                <StatusBadge status={String(job.status)} />
                <span>
                  <strong>{String(job.job_type || '-')}</strong>
                  <small>{String(job.target_key || '-')}</small>
                </span>
                <time>{formatDate(job.queued_at)}</time>
              </button>
            ))}
          </div>
        </section>
      </div>
    </>
  );
}
