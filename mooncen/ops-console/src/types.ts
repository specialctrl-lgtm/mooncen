export type OpsStatus = 'healthy' | 'warning' | 'critical' | 'unknown' | 'disabled';
export type JobStatus =
  | 'queued'
  | 'assigned'
  | 'running'
  | 'success'
  | 'partial_success'
  | 'failed'
  | 'cancelled'
  | 'timed_out'
  | 'blocked';

export type OpsSession = {
  user: { id: string; email: string; name: string };
  role: 'viewer' | 'operator' | 'admin';
  environment: 'production' | 'staging' | 'development';
};

export type PageResponse<T> = {
  available: boolean;
  items: T[];
  total: number;
  limit: number;
  offset: number;
};

export type OpsService = Record<string, unknown> & {
  id: string;
  service_name: string;
  service_type: string;
  environment: string;
  status: string;
  service_host?: string | null;
  reporter_hostname?: string | null;
  observed_runtime_host?: string | null;
  runtime_host_verified?: boolean;
  runtime_host_evidence_source?: string | null;
  reporter_is_runtime_evidence?: boolean;
  service_host_is_runtime_evidence?: boolean;
  status_observation_source?: string | null;
  configured_owner_node?: string | null;
  configured_owner_host?: string | null;
  configured_owner_role?: string | null;
  topology_node?: string | null;
  topology_host?: string | null;
  topology_role?: string | null;
};

export type DashboardSummary = {
  generated_at: string;
  environment: string;
  overall_status: OpsStatus;
  components: Array<{
    type: string;
    name: string;
    status: OpsStatus;
    response_time_ms?: number | null;
    current_version?: string | null;
    current_commit?: string | null;
    last_checked_at?: string | null;
    service_host?: string | null;
    reporter_hostname?: string | null;
    observed_runtime_host?: string | null;
    runtime_host_verified?: boolean;
    runtime_host_evidence_source?: string | null;
    reporter_is_runtime_evidence?: boolean;
    service_host_is_runtime_evidence?: boolean;
    status_observation_source?: string | null;
    configured_owner_node?: string | null;
    configured_owner_host?: string | null;
    configured_owner_role?: string | null;
    topology_node?: string | null;
    topology_host?: string | null;
    topology_role?: string | null;
  }>;
  agents: { connected: number; total: number; status: OpsStatus };
  latest_deployment?: Record<string, unknown> | null;
  grafana_url?: string | null;
};

export type CollectionSummary = {
  available: boolean;
  today: {
    collected: number;
    new: number;
    updated: number;
    failed: number;
    deleted_candidates: number;
    running: number;
  };
  providers: Array<Record<string, unknown>>;
  last_collection_at?: string | null;
};

export type QualitySummary = {
  available: boolean;
  counts: Record<string, number>;
  issue_statuses: Array<Record<string, unknown>>;
  latest_scan_at?: string | null;
  rule_source: string;
};

export type CrawlerQualityItem = {
  provider: string;
  content_type: string;
  active_courses: number;
  branch_count: number;
  cqs: number | null;
  cqs_measurable: boolean;
  cqs_breakdown: {
    completeness: { score: number; weight: number; rate: number | null; measurable: boolean; latest_count: number; prev_count: number };
    accuracy: { score: number | null; weight: number; rate: number | null; measurable: boolean; note?: string };
    required_fields: { score: number; weight: number; rate: number; measurable: boolean; valid_count: number; total_count: number };
    application_url: { score: number; weight: number; rate: number; measurable: boolean; valid_count: number; missing_count: number };
    freshness: { score: number; weight: number; rate: number; measurable: boolean; fresh_7d_count: number };
    duplicate_prevention: { score: number; weight: number; rate: number; measurable: boolean; duplicate_count: number };
    stability: { score: number; weight: number; rate: number | null; measurable: boolean; run_count: number };
  };
  anomaly: {
    status: 'NORMAL' | 'CAUTION' | 'WARNING' | 'CRITICAL' | 'ZERO_DROP' | 'UNKNOWN';
    drop_rate: number;
    reason: string;
    latest_count: number;
    prev_count: number;
  };
  funnel: {
    listing_count: number;
    detail_attempted: number;
    detail_success: number;
    parsed_count: number;
    valid_count: number;
    duplicate_count: number;
    error_count: number;
  };
  dqs: number | null;
  dqs_grade: string;
  dqs_measurable: boolean;
  dqs_metrics: {
    title_rate: number;
    branch_rate: number;
    schedule_rate: number;
    fee_rate: number;
    url_rate: number;
    logical_rate: number;
    fresh_rate: number;
    date_reversed_count: number;
    time_reversed_count: number;
    price_anomaly_count: number;
  };
  latest_run?: {
    id: number;
    status: string;
    started_at: string;
    duration_seconds: number;
    collected_count: number;
    error_type?: string;
  } | null;
};

export type BranchQualityItem = {
  branch_id: string;
  branch_code?: string | null;
  branch_name: string;
  address?: string | null;
  has_coords: boolean;
  active_courses: number;
  valid_courses: number;
  valid_rate: number;
  url_rate: number;
  status: string;
  date_anomalies: number;
};

export type RegressionComparison = {
  target_key: string;
  verdict: 'IMPROVED' | 'STABLE' | 'REGRESSION';
  reasons: string[];
  current_run: {
    id: number;
    started_at?: string | null;
    status: string;
    collected_count: number;
    duration_seconds: number;
    error_type?: string | null;
  };
  previous_run: {
    id: number;
    started_at?: string | null;
    status: string;
    collected_count: number;
    duration_seconds: number;
    error_type?: string | null;
  };
  diff: {
    collected_delta: number;
    collected_delta_pct: number;
    duration_delta: number;
  };
};


export type VisitorPeriodSummary = {
  start_date: string;
  end_date: string;
  visits: number;
  requests: number;
  partial: boolean;
  estimated: boolean;
};

export type VisitorSeriesPoint = {
  date: string;
  visits: number;
  requests: number;
  partial: boolean;
  estimated: boolean;
};

type VisitorSummaryMetadata = {
  schema_version: number;
  timezone: string;
  requested_days: number;
  estimated: boolean;
  source: {
    provider: string;
    dataset: string;
    hostname: string;
    hostnames: string[];
    request_source: string;
    granularity: string;
    adaptive_sampling: boolean;
    values_are_estimates: boolean;
  };
  sampling: {
    method: string;
    confidence_level: number;
    confidence_intervals_requested: boolean;
    validated_points: number;
    max_sample_interval: number | null;
    min_sample_size: number | null;
    aggregate_bounds_available: boolean;
  };
  metric_definitions: Record<string, {
    label?: string;
    description?: string;
    unique_visitors?: boolean;
    pageviews?: boolean;
    available?: boolean;
    estimated?: boolean;
    reason_code?: string;
  }>;
  generated_at: string;
};

export type VisitorSummary = VisitorSummaryMetadata & (
  | {
      available: true;
      reason_code?: null;
      summary: {
        today: VisitorPeriodSummary;
        yesterday: VisitorPeriodSummary;
        last_7_days: VisitorPeriodSummary;
        previous_7_days: VisitorPeriodSummary | null;
      };
      series: VisitorSeriesPoint[];
      data_through: string;
    }
  | {
      available: false;
      reason_code?: string | null;
      summary: null;
      series: [];
      data_through: null;
    }
);

export type CrawlerRun = {
  id: string;
  crawler_name: string;
  content_type: string;
  provider?: string | null;
  branch?: string | null;
  status: string;
  run_mode: string;
  total_count: number;
  success_count: number;
  failed_count: number;
  new_count: number;
  updated_count: number;
  started_at?: string | null;
  finished_at?: string | null;
  job_id?: string | null;
  trigger?: string;
  source?: string;
};

export type OpsJob = {
  id: string;
  job_type: string;
  status: JobStatus;
  environment: string;
  target_key?: string | null;
  progress: number;
  error_code?: string | null;
  error_message?: string | null;
  queued_at: string;
  started_at?: string | null;
  finished_at?: string | null;
};
