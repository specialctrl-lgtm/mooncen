import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { ColumnDef } from '@tanstack/react-table';
import { useEffect, useMemo, useState, type FormEvent } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router';
import { OpsApiError, opsApi } from '../api';
import DataTable from '../components/DataTable';
import StatusBadge from '../components/StatusBadge';
import { DefinitionList, DetailPanel, PageHeader, QueryState } from '../components/Ui';
import { useOpsSession } from '../context';
import type { CrawlerRun, OpsSession, PageResponse } from '../types';
import { formatDate, formatNumber } from '../utils';

type CrawlerProvider = Record<string, unknown> & {
  provider: string;
  crawler_name: string;
  content_type: string;
  status: string;
  can_run: boolean;
  run_blocked_reason?: string | null;
};

type StudioCapability = { available: boolean; reason?: string | null };
type StudioCapabilities = Record<string, StudioCapability>;
type StudioCapabilitiesResponse = {
  available: boolean;
  environment?: OpsSession['environment'];
  role?: OpsSession['role'];
  capabilities?: StudioCapabilities;
};
type StudioProviderPath = { provider: string; source_path: string; impacted_providers: string[] };
type StudioProvidersResponse = {
  available: boolean;
  items: StudioProviderPath[];
  total: number;
};
type StudioDraft = {
  id: string;
  environment: OpsSession['environment'];
  provider: string;
  source_path: string;
  title: string;
  status: 'draft' | 'in_review' | 'approved' | 'changes_requested' | 'archived';
  latest_revision: number;
  impacted_providers: string[];
  created_by: string;
  created_at: string;
  updated_at: string;
};
type StudioRevision = {
  id: string;
  draft_id: string;
  environment: OpsSession['environment'];
  revision: number;
  source_sha256: string;
  source_size_bytes: number;
  impacted_providers: string[];
  source_text: string;
  change_summary: string;
  created_by: string;
  created_at: string;
};
type StudioReview = {
  id: string;
  draft_id: string;
  environment: OpsSession['environment'];
  revision: number;
  decision: 'submit' | 'approve' | 'request_changes' | 'archive';
  comment: string;
  reviewed_by: string;
  created_at: string;
};
type StudioDraftDetail = StudioDraft & {
  latest_revision_item: StudioRevision | null;
  reviews: StudioReview[];
};
type StudioDraftDetailResponse = {
  available: boolean;
  item: StudioDraftDetail | null;
  capabilities?: StudioCapabilities;
};
type StudioDraftPage = PageResponse<StudioDraft> & { available: boolean };
type StudioRevisionPage = PageResponse<StudioRevision> & { available: boolean };
type RunMode = 'dry_run' | 'review';
type SourceIdentity = {
  source: string;
  sha256: string;
  sizeBytes: number;
  pending: boolean;
  error: string | null;
};

const MAX_SOURCE_BYTES = 512 * 1024;

function centralRoutingUnavailable(error: unknown, environment: string): boolean {
  return error instanceof OpsApiError
    && (error.status === 503 || (environment !== 'development' && error.status === 409));
}

function supportedContentType(value: string): string {
  return ['culture_center', 'experience', 'education'].includes(value) ? value : 'all';
}

function hasUnpairedSurrogate(value: string): boolean {
  for (let index = 0; index < value.length; index += 1) {
    const unit = value.charCodeAt(index);
    if (unit >= 0xd800 && unit <= 0xdbff) {
      const next = value.charCodeAt(index + 1);
      if (!(next >= 0xdc00 && next <= 0xdfff)) return true;
      index += 1;
    } else if (unit >= 0xdc00 && unit <= 0xdfff) {
      return true;
    }
  }
  return false;
}

async function exactUtf8Sha256(source: string): Promise<{ sha256: string; sizeBytes: number }> {
  if (source.includes('\0') || hasUnpairedSurrogate(source)) {
    throw new Error('소스는 NUL이나 잘못된 유니코드 문자를 포함할 수 없습니다.');
  }
  const bytes = new TextEncoder().encode(source);
  if (!window.crypto?.subtle) throw new Error('브라우저의 SHA-256 기능을 사용할 수 없습니다.');
  const digest = await window.crypto.subtle.digest('SHA-256', bytes);
  const sha256 = Array.from(new Uint8Array(digest), (item) => item.toString(16).padStart(2, '0')).join('');
  return { sha256, sizeBytes: bytes.byteLength };
}

function useSourceIdentity(source: string): SourceIdentity {
  const [identity, setIdentity] = useState<SourceIdentity>({
    source,
    sha256: '',
    sizeBytes: 0,
    pending: Boolean(source),
    error: null,
  });

  useEffect(() => {
    let active = true;
    setIdentity({ source, sha256: '', sizeBytes: 0, pending: Boolean(source), error: null });
    if (!source) return () => { active = false; };
    void exactUtf8Sha256(source).then(
      ({ sha256, sizeBytes }) => {
        if (active) setIdentity({ source, sha256, sizeBytes, pending: false, error: null });
      },
      (error: unknown) => {
        if (active) {
          setIdentity({
            source,
            sha256: '',
            sizeBytes: 0,
            pending: false,
            error: error instanceof Error ? error.message : 'SHA-256 계산에 실패했습니다.',
          });
        }
      },
    );
    return () => { active = false; };
  }, [source]);

  return identity;
}

function SourceDigest({ identity }: { identity: SourceIdentity }) {
  const invalidSize = identity.source.length > 0
    && (identity.sizeBytes < 1 || identity.sizeBytes > MAX_SOURCE_BYTES);
  return (
    <div className={`studio-source-identity ${identity.error || invalidSize ? 'identity-error' : ''}`}>
      <span>정확한 UTF-8 SHA-256</span>
      <code>{identity.pending ? '계산 중…' : identity.sha256 || '소스를 입력하세요'}</code>
      <small>{formatNumber(identity.sizeBytes)} / {formatNumber(MAX_SOURCE_BYTES)} bytes</small>
      {identity.error && <strong role="alert">{identity.error}</strong>}
      {invalidSize && <strong role="alert">소스는 UTF-8 기준 1~524,288 bytes여야 합니다.</strong>}
    </div>
  );
}

function capabilityAvailable(capabilities: StudioCapabilities | undefined, key: string): boolean {
  return capabilities?.[key]?.available === true;
}

function capabilityReason(capabilities: StudioCapabilities | undefined, key: string): string {
  return String(capabilities?.[key]?.reason || '서버가 이 기능을 허용하지 않습니다.');
}

function capabilityLabel(key: string): string {
  return {
    draft_storage: '초안 저장',
    revision_history: '리비전 이력',
    review_decision: '소스 리뷰',
    source_approval: '독립 소스 승인',
    fixture_validation: 'Fixture 검증',
    source_execution: '소스 실행',
    build: '빌드',
    sign: '서명',
    independent_release_approval: '독립 릴리스 승인',
  }[key] || key;
}

export default function CrawlerStudioPage() {
  const session = useOpsSession();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { id } = useParams();
  const [searchParams] = useSearchParams();
  const requestedProvider = (searchParams.get('provider') || '').trim().slice(0, 100);

  const [validationProvider, setValidationProvider] = useState(requestedProvider);
  const [runMode, setRunMode] = useState<RunMode>('dry_run');
  const [probeUrl, setProbeUrl] = useState('');
  const [probeTimeout, setProbeTimeout] = useState(25);
  const [compareExisting, setCompareExisting] = useState(true);
  const [saveHtml, setSaveHtml] = useState(false);
  const [saveScreenshot, setSaveScreenshot] = useState(false);

  const [draftProvider, setDraftProvider] = useState(requestedProvider);
  const [draftSourcePath, setDraftSourcePath] = useState('');
  const [draftTitle, setDraftTitle] = useState('');
  const [draftSource, setDraftSource] = useState('');
  const [draftSummary, setDraftSummary] = useState('');
  const [selectedDraftId, setSelectedDraftId] = useState('');
  const [revisionSource, setRevisionSource] = useState('');
  const [revisionSummary, setRevisionSummary] = useState('');
  const [reviewComment, setReviewComment] = useState('');

  const draftIdentity = useSourceIdentity(draftSource);
  const revisionIdentity = useSourceIdentity(revisionSource);

  const studioCapabilities = useQuery({
    queryKey: ['crawler-studio-capabilities', session.environment, session.role],
    queryFn: () => opsApi<StudioCapabilitiesResponse>('/crawler-studio/capabilities'),
    refetchInterval: 30_000,
  });
  const studioProviders = useQuery({
    queryKey: ['crawler-studio-provider-paths', session.environment],
    queryFn: () => opsApi<StudioProvidersResponse>('/crawler-studio/providers'),
    refetchInterval: 60_000,
  });
  const studioDrafts = useQuery({
    queryKey: ['crawler-studio-drafts', session.environment],
    queryFn: () => opsApi<StudioDraftPage>('/crawler-studio/drafts?limit=100&offset=0'),
    refetchInterval: 15_000,
  });
  const studioDetail = useQuery({
    queryKey: ['crawler-studio-draft', session.environment, selectedDraftId],
    queryFn: () => opsApi<StudioDraftDetailResponse>(`/crawler-studio/drafts/${selectedDraftId}`),
    enabled: Boolean(selectedDraftId),
  });
  const studioRevisions = useQuery({
    queryKey: ['crawler-studio-revisions', session.environment, selectedDraftId],
    queryFn: () => opsApi<StudioRevisionPage>(`/crawler-studio/drafts/${selectedDraftId}/revisions?limit=100&offset=0`),
    enabled: Boolean(selectedDraftId),
  });

  const providers = useQuery({
    queryKey: ['crawler-studio-validation-providers', session.environment],
    queryFn: () => opsApi<{ available: boolean; registry_available?: boolean; items: CrawlerProvider[] }>('/crawlers'),
    refetchInterval: 30_000,
  });
  const runs = useQuery({
    queryKey: ['crawler-studio-runs', session.environment, validationProvider],
    queryFn: () => opsApi<PageResponse<CrawlerRun>>(
      validationProvider
        ? `/crawlers/runs?limit=100&provider=${encodeURIComponent(validationProvider)}`
        : '/crawlers/runs?limit=100',
    ),
    refetchInterval: 15_000,
  });
  const detail = useQuery({
    queryKey: ['crawler-studio-run', session.environment, id],
    queryFn: () => opsApi<Record<string, unknown>>(`/crawlers/runs/${id}`),
    enabled: Boolean(id),
    refetchInterval: id ? 5_000 : false,
  });
  const runJobId = detail.data?.job_id ? String(detail.data.job_id) : '';
  const logs = useQuery({
    queryKey: ['crawler-studio-run-logs', session.environment, id, runJobId],
    queryFn: () => opsApi<{ available: boolean; items: Array<Record<string, unknown>> }>(
      `/jobs/${runJobId}/logs?limit=1000&tail=true`,
    ),
    enabled: Boolean(id && runJobId),
    refetchInterval: id && ['queued', 'assigned', 'running'].includes(String(detail.data?.status)) ? 5_000 : false,
  });
  const errors = useQuery({
    queryKey: ['crawler-studio-run-errors', session.environment, id],
    queryFn: () => opsApi<{ available: boolean; items: Array<Record<string, unknown>> }>(`/crawlers/runs/${id}/errors`),
    enabled: Boolean(id),
  });

  useEffect(() => {
    if (providers.data?.items.length && !providers.data.items.some((item) => item.provider === validationProvider)) {
      const exactProvider = providers.data.items.find((item) => item.provider === requestedProvider);
      setValidationProvider(exactProvider?.provider || (requestedProvider ? requestedProvider : providers.data.items[0].provider));
    }
  }, [requestedProvider, validationProvider, providers.data?.items]);

  const reviewedProviders = useMemo(
    () => Array.from(new Set((studioProviders.data?.available === true ? studioProviders.data.items : [])
      .map((item) => item.provider))),
    [studioProviders.data],
  );
  const requestedDraftProviderUnavailable = Boolean(
    requestedProvider
    && studioProviders.data?.available === true
    && !reviewedProviders.includes(requestedProvider),
  );
  const reviewedPaths = useMemo(
    () => (studioProviders.data?.available === true ? studioProviders.data.items : [])
      .filter((item) => item.provider === draftProvider)
      .map((item) => item.source_path),
    [draftProvider, studioProviders.data],
  );
  const selectedPathImpactedProviders = useMemo(
    () => Array.from(new Set((studioProviders.data?.available === true ? studioProviders.data.items : [])
      .filter((item) => item.source_path === draftSourcePath)
      .flatMap((item) => item.impacted_providers))).sort(),
    [draftSourcePath, studioProviders.data],
  );

  useEffect(() => {
    if (reviewedProviders.length && !reviewedProviders.includes(draftProvider)) {
      if (requestedDraftProviderUnavailable) return;
      setDraftProvider(reviewedProviders.includes(requestedProvider) ? requestedProvider : reviewedProviders[0]);
    }
  }, [draftProvider, requestedDraftProviderUnavailable, requestedProvider, reviewedProviders]);
  useEffect(() => {
    if (!reviewedPaths.includes(draftSourcePath)) setDraftSourcePath(reviewedPaths[0] || '');
  }, [draftSourcePath, reviewedPaths]);
  useEffect(() => {
    if (!selectedDraftId && studioDrafts.data?.available === true && studioDrafts.data.items.length) {
      setSelectedDraftId(studioDrafts.data.items[0].id);
    }
  }, [selectedDraftId, studioDrafts.data]);

  const selectedDraft = studioDetail.data?.available === true ? studioDetail.data.item : null;
  const selectedDraftRevision = selectedDraft?.latest_revision ?? null;
  const selectedDraftSourceText = selectedDraft?.latest_revision_item?.source_text || '';
  useEffect(() => {
    if (selectedDraftRevision === null) return;
    setRevisionSource(selectedDraftSourceText);
    setRevisionSummary('');
    setReviewComment('');
  }, [selectedDraftId, selectedDraftRevision, selectedDraftSourceText]);

  const refreshStudio = (draftId?: string) => {
    void queryClient.invalidateQueries({ queryKey: ['crawler-studio-drafts', session.environment] });
    if (draftId) {
      void queryClient.invalidateQueries({ queryKey: ['crawler-studio-draft', session.environment, draftId] });
      void queryClient.invalidateQueries({ queryKey: ['crawler-studio-revisions', session.environment, draftId] });
    }
  };

  const createDraft = useMutation<StudioDraftDetailResponse, Error, {
    provider: string;
    sourcePath: string;
    title: string;
    sourceText: string;
    sourceSha256: string;
    changeSummary: string;
  }>({
    mutationFn: (input) => opsApi('/crawler-studio/drafts', {
      method: 'POST',
      body: JSON.stringify({
        provider: input.provider,
        source_path: input.sourcePath,
        title: input.title,
        source_text: input.sourceText,
        source_sha256: input.sourceSha256,
        change_summary: input.changeSummary,
      }),
    }),
    onSuccess: (result) => {
      if (result.item?.id) setSelectedDraftId(result.item.id);
      refreshStudio(result.item?.id);
      setDraftTitle('');
      setDraftSource('');
      setDraftSummary('');
    },
  });
  const appendRevision = useMutation<StudioDraftDetailResponse, Error, {
    draftId: string;
    expectedRevision: number;
    sourceText: string;
    sourceSha256: string;
    changeSummary: string;
  }>({
    mutationFn: (input) => opsApi(`/crawler-studio/drafts/${input.draftId}/revisions`, {
      method: 'POST',
      body: JSON.stringify({
        expected_revision: input.expectedRevision,
        source_text: input.sourceText,
        source_sha256: input.sourceSha256,
        change_summary: input.changeSummary,
      }),
    }),
    onSuccess: (result) => {
      if (result.item?.id) refreshStudio(result.item.id);
    },
  });
  const reviewDraft = useMutation<StudioDraftDetailResponse, Error, {
    draftId: string;
    expectedRevision: number;
    expectedSourceSha256: string;
    decision: StudioReview['decision'];
    comment: string;
  }>({
    mutationFn: (input) => opsApi(`/crawler-studio/drafts/${input.draftId}/reviews`, {
      method: 'POST',
      body: JSON.stringify({
        expected_revision: input.expectedRevision,
        expected_source_sha256: input.expectedSourceSha256,
        decision: input.decision,
        comment: input.comment,
      }),
    }),
    onSuccess: (result) => {
      if (result.item?.id) refreshStudio(result.item.id);
      setReviewComment('');
    },
  });

  const runMutation = useMutation<
    { job: { id: string }; crawler_run: { id: string } },
    Error,
    Record<string, unknown>
  >({
    mutationFn: (body) => opsApi('/crawlers/run', { method: 'POST', body: JSON.stringify(body) }),
    onSuccess: (result) => {
      void queryClient.invalidateQueries({ queryKey: ['crawler-studio-runs', session.environment] });
      navigate(`/crawler-studio/runs/${result.crawler_run.id}`);
    },
  });
  const probeMutation = useMutation<{ job: { id: string } }, Error>({
    mutationFn: () => opsApi('/crawlers/parser-probe', {
      method: 'POST',
      body: JSON.stringify({ url: probeUrl.trim(), timeout: probeTimeout }),
    }),
    onSuccess: (result) => navigate(`/jobs/${result.job.id}`),
  });

  const capabilities = studioCapabilities.data?.available === true
    ? studioCapabilities.data.capabilities
    : undefined;
  const roleMatches = studioCapabilities.data?.available === true
    && studioCapabilities.data.role === session.role;
  const environmentMatches = studioCapabilities.data?.available === true
    && studioCapabilities.data.environment === session.environment;
  const selectedDraftEnvironmentMatches = selectedDraft?.environment === session.environment;
  const canStoreDraft = environmentMatches
    && roleMatches
    && session.role !== 'viewer'
    && capabilityAvailable(capabilities, 'draft_storage');
  const canAppendRevision = environmentMatches
    && roleMatches
    && session.role !== 'viewer'
    && capabilityAvailable(capabilities, 'revision_history')
    && Boolean(selectedDraftEnvironmentMatches && selectedDraft && selectedDraft.status !== 'in_review');
  const canReview = environmentMatches
    && roleMatches
    && session.role !== 'viewer'
    && capabilityAvailable(capabilities, 'review_decision');
  const canApproveSource = canReview
    && session.role === 'admin'
    && capabilityAvailable(capabilities, 'source_approval');
  const draftIdentityCurrent = draftIdentity.source === draftSource && !draftIdentity.pending;
  const revisionIdentityCurrent = revisionIdentity.source === revisionSource && !revisionIdentity.pending;
  const editorMatchesStoredRevision = Boolean(
    selectedDraftEnvironmentMatches
      && revisionIdentityCurrent
      && revisionIdentity.sha256
      && revisionIdentity.sha256 === selectedDraft?.latest_revision_item?.source_sha256,
  );

  const submitDraft = (event: FormEvent) => {
    event.preventDefault();
    if (
      !canStoreDraft
      || requestedDraftProviderUnavailable
      || !reviewedProviders.includes(draftProvider)
      || !reviewedPaths.includes(draftSourcePath)
      || draftTitle.trim().length < 3
      || draftSummary.trim().length < 3
      || !draftIdentityCurrent
      || !draftIdentity.sha256
    ) return;
    if (draftIdentity.sizeBytes < 1 || draftIdentity.sizeBytes > MAX_SOURCE_BYTES) return;
    createDraft.mutate({
      provider: draftProvider,
      sourcePath: draftSourcePath,
      title: draftTitle.trim(),
      sourceText: draftSource,
      sourceSha256: draftIdentity.sha256,
      changeSummary: draftSummary.trim(),
    });
  };
  const submitRevision = (event: FormEvent) => {
    event.preventDefault();
    if (
      !selectedDraft
      || !canAppendRevision
      || revisionSummary.trim().length < 3
      || !revisionIdentityCurrent
      || !revisionIdentity.sha256
    ) return;
    if (revisionIdentity.sizeBytes < 1 || revisionIdentity.sizeBytes > MAX_SOURCE_BYTES) return;
    appendRevision.mutate({
      draftId: selectedDraft.id,
      expectedRevision: selectedDraft.latest_revision,
      sourceText: revisionSource,
      sourceSha256: revisionIdentity.sha256,
      changeSummary: revisionSummary.trim(),
    });
  };
  const decideReview = (decision: StudioReview['decision']) => {
    if (!selectedDraft || !canReview || !editorMatchesStoredRevision || reviewComment.trim().length < 3) return;
    if (decision === 'approve' && !canApproveSource) return;
    reviewDraft.mutate({
      draftId: selectedDraft.id,
      expectedRevision: selectedDraft.latest_revision,
      expectedSourceSha256: revisionIdentity.sha256,
      decision,
      comment: reviewComment.trim(),
    });
  };

  const selectedValidationProvider = providers.data?.items.find(
    (item) => item.provider === validationProvider,
  );
  const requestedValidationProviderUnavailable = Boolean(
    requestedProvider
    && providers.data?.available === true
    && !providers.data.items.some((item) => item.provider === requestedProvider),
  );
  const selectedRuns = useMemo(
    () => (runs.data?.items || [])
      .filter((run) => (!validationProvider || run.provider === validationProvider)
        && ['dry_run', 'review'].includes(run.run_mode))
      .slice(0, 20),
    [validationProvider, runs.data?.items],
  );
  const submitRun = (event: FormEvent) => {
    event.preventDefault();
    if (!selectedValidationProvider || !selectedValidationProvider.can_run || session.role === 'viewer') return;
    runMutation.mutate({
      scope: 'provider',
      provider: selectedValidationProvider.provider.trim().toUpperCase(),
      content_type: supportedContentType(selectedValidationProvider.content_type),
      run_mode: runMode,
      compare_existing: compareExisting,
      review_before_apply: runMode === 'review',
      save_html: saveHtml,
      save_screenshot: saveScreenshot,
      browser_visible: false,
      max_retries: 0,
      concurrency: 1,
      force_full_refresh: false,
    });
  };

  const draftColumns = useMemo<ColumnDef<StudioDraft>[]>(() => [
    {
      accessorKey: 'title',
      header: '초안',
      cell: ({ row }) => (
        <button className="studio-draft-link" type="button" onClick={() => setSelectedDraftId(row.original.id)}>
          {row.original.title}
        </button>
      ),
    },
    { accessorKey: 'provider', header: '영향 Provider', cell: ({ row }) => row.original.impacted_providers.join(', ') },
    { accessorKey: 'status', header: '상태', cell: ({ row }) => <StatusBadge status={row.original.status} /> },
    { accessorKey: 'latest_revision', header: '리비전' },
    { accessorKey: 'updated_at', header: '수정', cell: ({ row }) => formatDate(row.original.updated_at) },
  ], []);
  const runColumns = useMemo<ColumnDef<CrawlerRun>[]>(() => [
    { accessorKey: 'provider', header: 'Provider' },
    { accessorKey: 'run_mode', header: '검증 방식' },
    { accessorKey: 'status', header: '상태', cell: ({ row }) => <StatusBadge status={row.original.status} /> },
    { accessorKey: 'total_count', header: '수집', cell: ({ row }) => formatNumber(row.original.total_count) },
    { accessorKey: 'new_count', header: '신규', cell: ({ row }) => formatNumber(row.original.new_count) },
    { accessorKey: 'updated_count', header: '변경', cell: ({ row }) => formatNumber(row.original.updated_count) },
    { accessorKey: 'failed_count', header: '실패', cell: ({ row }) => formatNumber(row.original.failed_count) },
    { accessorKey: 'started_at', header: '시작', cell: ({ row }) => formatDate(row.original.started_at) },
  ], []);
  const registryRoutingBlocked = session.environment !== 'development'
    && selectedValidationProvider?.can_run === false
    && /distributed|local.+runtime|runtime.+disabled/i.test(
      String(selectedValidationProvider.run_blocked_reason || ''),
    );
  const routingBlocked = registryRoutingBlocked
    || centralRoutingUnavailable(runMutation.error || probeMutation.error, session.environment);
  const reviewBusy = reviewDraft.isPending;

  return (
    <>
      <PageHeader
        eyebrow="CENTRAL CRAWLER STUDIO"
        title="Crawler Studio"
        description="중앙 서버에 Provider allowlist 기반 소스 초안과 append-only 리비전·리뷰 근거를 저장합니다. 이 화면은 소스를 실행하거나 빌드·서명·배포하지 않습니다."
        actions={(
          <>
            <Link className="button subtle" to="/crawlers">실행 운영 · 이력</Link>
            <Link className="button subtle" to="/crawler-improvements">개선 큐</Link>
            <Link className="button subtle" to="/data-quality">품질 분석</Link>
            <Link className="button subtle" to="/crawler-releases">릴리스</Link>
          </>
        )}
      />

      <section className="panel studio-capability-panel">
        <header className="section-header">
          <div>
            <h2>중앙 Studio 권한 경계</h2>
            <small>소스 승인은 릴리스 승인이 아니며, 실행·빌드·서명 기능을 부여하지 않습니다.</small>
          </div>
          {studioCapabilities.data?.available === true && (
            <StatusBadge status={studioCapabilities.data.environment === session.environment ? 'ready' : 'blocked'} />
          )}
        </header>
        <QueryState
          loading={studioCapabilities.isLoading}
          error={studioCapabilities.error}
          unavailable={studioCapabilities.data?.available === false}
        />
        {studioCapabilities.data?.available === true && (
          <>
            {studioCapabilities.data.environment !== session.environment || !roleMatches ? (
              <div className="deploy-blockers" role="alert">Studio 환경 또는 역할 계약이 현재 Ops 세션과 다릅니다. 쓰기 기능을 사용할 수 없습니다.</div>
            ) : null}
            <div className="studio-capability-grid">
              {['draft_storage', 'revision_history', 'review_decision', 'source_approval', 'fixture_validation', 'source_execution', 'build', 'sign', 'independent_release_approval'].map((key) => {
                const capability = capabilities?.[key];
                return (
                  <article key={key} className={capability?.available ? 'capability-on' : 'capability-off'}>
                    <span>{capabilityLabel(key)}</span>
                    <strong>{capability?.available ? '사용 가능' : '비활성'}</strong>
                    {!capability?.available && <small>{capabilityReason(capabilities, key)}</small>}
                  </article>
                );
              })}
            </div>
          </>
        )}
      </section>

      <section className="studio-workflow" aria-label="중앙 크롤러 소스 검토 흐름">
        <article><span>1</span><strong>Allowlist 선택</strong><small>Provider · 고정 경로</small></article>
        <article><span>2</span><strong>초안 · SHA-256</strong><small>정확한 UTF-8 근거</small></article>
        <article><span>3</span><strong>Append-only 리비전</strong><small>expected_revision fence</small></article>
        <article><span>4</span><strong>소스 리뷰</strong><small>릴리스 승인과 분리</small></article>
      </section>

      <div className="studio-columns studio-authoring-columns">
        <section className="panel">
          <header className="section-header">
            <div><h2>새 중앙 초안</h2><small>서버가 검토한 Provider와 경로만 선택할 수 있습니다.</small></div>
          </header>
          <QueryState
            loading={studioProviders.isLoading}
            error={studioProviders.error}
            unavailable={studioProviders.data?.available === false}
            empty={studioProviders.data?.available === true && studioProviders.data.items.length === 0}
          />
          {studioProviders.data?.available === true && studioProviders.data.items.length > 0 && (
            <form className="stack-form studio-source-form" onSubmit={submitDraft}>
              {requestedDraftProviderUnavailable ? (
                <div className="deploy-blockers" role="alert" id="studio-provider-unavailable">
                  요청한 Provider {requestedProvider}는 Crawler Studio의 검토 목록에 없습니다. 다른 Provider로 자동 전환하지 않았으며, 이 딥링크에서는 초안을 만들 수 없습니다.
                </div>
              ) : null}
              <div className="studio-identity-selectors">
                <label>
                  검토된 Provider
                  <select
                    value={draftProvider}
                    onChange={(event) => setDraftProvider(event.target.value)}
                    disabled={requestedDraftProviderUnavailable}
                    aria-describedby={requestedDraftProviderUnavailable ? 'studio-provider-unavailable' : undefined}
                  >
                    {requestedDraftProviderUnavailable ? (
                      <option value={requestedProvider}>{requestedProvider} · Studio 검토 목록 없음</option>
                    ) : null}
                    {reviewedProviders.map((provider) => <option key={provider} value={provider}>{provider}</option>)}
                  </select>
                </label>
                <label>
                  검토된 소스 경로
                  <select value={draftSourcePath} onChange={(event) => setDraftSourcePath(event.target.value)}>
                    {reviewedPaths.map((path) => <option key={path} value={path}>{path}</option>)}
                  </select>
                </label>
              </div>
              <label>
                초안 제목
                <input value={draftTitle} onChange={(event) => setDraftTitle(event.target.value)} minLength={3} maxLength={160} required />
              </label>
              <label>
                소스 편집기
                <textarea className="studio-source-editor" value={draftSource} onChange={(event) => setDraftSource(event.target.value)} maxLength={MAX_SOURCE_BYTES} required spellCheck={false} aria-describedby="new-draft-source-boundary" />
              </label>
              <p className="form-note" id="new-draft-source-boundary">경로와 실행 명령은 편집할 수 없습니다. 이 소스는 중앙 DB에 초안으로만 저장됩니다.</p>
              {selectedPathImpactedProviders.length > 0 && (
                <p className="form-note"><strong>영향 Provider:</strong> {selectedPathImpactedProviders.join(', ')}</p>
              )}
              <SourceDigest identity={draftIdentity} />
              <label>
                변경 요약
                <textarea value={draftSummary} onChange={(event) => setDraftSummary(event.target.value)} minLength={3} maxLength={500} required />
              </label>
              <button className="button primary" type="submit" disabled={
                !canStoreDraft
                || createDraft.isPending
                || requestedDraftProviderUnavailable
                || !reviewedProviders.includes(draftProvider)
                || !reviewedPaths.includes(draftSourcePath)
                || !draftIdentityCurrent
                || !draftIdentity.sha256
                || draftIdentity.sizeBytes > MAX_SOURCE_BYTES
              }>
                {createDraft.isPending ? '초안 저장 중…' : '중앙 초안 저장'}
              </button>
              {session.role === 'viewer' && <p className="form-note">Viewer는 중앙 초안을 조회만 할 수 있습니다.</p>}
              {createDraft.error && <QueryState error={createDraft.error} />}
            </form>
          )}
        </section>

        <section className="panel">
          <header className="section-header">
            <div>
              <h2>중앙 초안 목록</h2>
              <small>{studioDrafts.data?.available === true ? `실제 ${formatNumber(studioDrafts.data.total)}건` : '중앙 저장소 상태 확인 필요'}</small>
            </div>
          </header>
          <QueryState
            loading={studioDrafts.isLoading}
            error={studioDrafts.error}
            unavailable={studioDrafts.data?.available === false}
            empty={studioDrafts.data?.available === true && studioDrafts.data.items.length === 0}
          />
          {studioDrafts.data?.available === true && studioDrafts.data.items.length > 0 && (
            <DataTable
              data={studioDrafts.data.items}
              columns={draftColumns}
              exportName="mooncen-crawler-studio-drafts.csv"
              onRowClick={(draft) => setSelectedDraftId(draft.id)}
            />
          )}
        </section>
      </div>

      {selectedDraftId && (
        <section className="panel studio-draft-detail">
          <header className="section-header">
            <div>
              <h2>선택한 초안 · 리비전 · 리뷰</h2>
              <small>리비전 추가와 리뷰 요청은 현재 latest_revision을 낙관적 fence로 전송합니다.</small>
            </div>
            {selectedDraft && <StatusBadge status={selectedDraft.status} />}
          </header>
          <QueryState
            loading={studioDetail.isLoading}
            error={studioDetail.error}
            unavailable={studioDetail.data?.available === false}
          />
          {selectedDraft && (
            <>
              <dl className="studio-draft-identity">
                <div><dt>Provider</dt><dd>{selectedDraft.provider}</dd></div>
                <div><dt>고정 경로</dt><dd><code>{selectedDraft.source_path}</code></dd></div>
                <div><dt>영향 Provider</dt><dd>{selectedDraft.latest_revision_item?.impacted_providers.join(', ') || '확인 불가'}</dd></div>
                <div><dt>현재 리비전</dt><dd>{selectedDraft.latest_revision}</dd></div>
                <div><dt>환경</dt><dd>{selectedDraft.environment}</dd></div>
              </dl>
              <div className="studio-detail-columns">
                <form className="stack-form studio-source-form" onSubmit={submitRevision}>
                  <header><h3>리비전 편집기</h3><small>현재 r{selectedDraft.latest_revision} 다음 리비전만 추가할 수 있습니다.</small></header>
                  <label>
                    소스
                    <textarea className="studio-source-editor" value={revisionSource} onChange={(event) => setRevisionSource(event.target.value)} maxLength={MAX_SOURCE_BYTES} required spellCheck={false} />
                  </label>
                  <SourceDigest identity={revisionIdentity} />
                  <label>
                    변경 요약
                    <textarea value={revisionSummary} onChange={(event) => setRevisionSummary(event.target.value)} minLength={3} maxLength={500} required />
                  </label>
                  <button className="button primary" type="submit" disabled={
                    !canAppendRevision
                    || appendRevision.isPending
                    || !revisionIdentityCurrent
                    || !revisionIdentity.sha256
                    || revisionIdentity.sizeBytes > MAX_SOURCE_BYTES
                  }>
                    {appendRevision.isPending ? '리비전 추가 중…' : `r${selectedDraft.latest_revision + 1} 리비전 추가`}
                  </button>
                  {selectedDraft.status === 'in_review' && (
                    <p className="form-note">리뷰 중에는 새 리비전을 추가할 수 없습니다. 승인·보관된 초안은 새 리비전을 추가하면 draft 검토 주기로 다시 열립니다.</p>
                  )}
                  {appendRevision.error && <QueryState error={appendRevision.error} />}
                </form>

                <div className="stack-form studio-review-form">
                  <header><h3>소스 리뷰 상태 전이</h3><small>승인은 현재 소스 리비전에만 적용되며 배포를 승인하지 않습니다.</small></header>
                  <label>
                    리뷰 의견
                    <textarea value={reviewComment} onChange={(event) => setReviewComment(event.target.value)} minLength={3} maxLength={1000} required />
                  </label>
                  {!editorMatchesStoredRevision && (
                    <div className="deploy-blockers" role="alert">편집기의 소스가 저장된 최신 리비전과 다릅니다. 새 리비전으로 저장한 뒤 리뷰하세요.</div>
                  )}
                  <div className="button-row">
                    <button className="button subtle" type="button" disabled={!canReview || !editorMatchesStoredRevision || reviewBusy || !['draft', 'changes_requested'].includes(selectedDraft.status) || reviewComment.trim().length < 3} onClick={() => decideReview('submit')}>리뷰 제출</button>
                    <button className="button subtle" type="button" disabled={!canReview || !editorMatchesStoredRevision || reviewBusy || selectedDraft.status !== 'in_review' || reviewComment.trim().length < 3} onClick={() => decideReview('request_changes')}>변경 요청</button>
                    <button className="button primary" type="button" title={canApproveSource ? '현재 소스 리비전 승인' : capabilityReason(capabilities, 'source_approval')} disabled={!canApproveSource || !editorMatchesStoredRevision || reviewBusy || selectedDraft.status !== 'in_review' || reviewComment.trim().length < 3} onClick={() => decideReview('approve')}>소스 승인 · 독립 근거 필요</button>
                    <button className="button danger" type="button" disabled={!canReview || !editorMatchesStoredRevision || reviewBusy || selectedDraft.status === 'archived' || reviewComment.trim().length < 3} onClick={() => decideReview('archive')}>초안 보관</button>
                  </div>
                  {reviewDraft.error && <QueryState error={reviewDraft.error} />}
                  <div className="studio-review-history">
                    <h4>리뷰 이력</h4>
                    {selectedDraft.reviews.length === 0 ? <p className="form-note">아직 리뷰 근거가 없습니다.</p> : selectedDraft.reviews.map((review) => (
                      <article key={review.id}>
                        <StatusBadge status={review.decision} />
                        <strong>r{review.revision}</strong>
                        <span>{review.comment}</span>
                        <small>{formatDate(review.created_at)}</small>
                      </article>
                    ))}
                  </div>
                </div>
              </div>

              <div className="studio-revision-history">
                <h3>Append-only 리비전 이력</h3>
                <QueryState
                  loading={studioRevisions.isLoading}
                  error={studioRevisions.error}
                  unavailable={studioRevisions.data?.available === false}
                  empty={studioRevisions.data?.available === true && studioRevisions.data.items.length === 0}
                />
                {studioRevisions.data?.available === true && studioRevisions.data.items.map((revision) => (
                  <article key={revision.id}>
                    <strong>r{revision.revision}</strong>
                    <code>{revision.source_sha256}</code>
                    <span>영향: {revision.impacted_providers.join(', ')}</span>
                    <span>{revision.change_summary}</span>
                    <small>{formatNumber(revision.source_size_bytes)} bytes · {formatDate(revision.created_at)}</small>
                  </article>
                ))}
              </div>
            </>
          )}
        </section>
      )}

      <section className="studio-validation-boundary">
        <header>
          <span className="eyebrow">LEGACY VALIDATION</span>
          <h2>검증 실행</h2>
          <p>기존 parser probe와 dry_run/review 화면입니다. 중앙 분산 라우팅이 503을 반환하면 로컬 실행으로 우회하지 않습니다.</p>
        </header>
      </section>

      {routingBlocked ? (
        <div className="deploy-blockers crawler-routing-block" role="alert">
          <strong>중앙 작업 라우팅 준비 중</strong>
          <span>이 환경에서는 브라우저가 로컬 크롤러를 직접 실행하지 않습니다. 중앙 서버의 probe/dry-run 작업 라우팅이 연결된 뒤 다시 시도하세요.</span>
        </div>
      ) : (
        (runMutation.error || probeMutation.error) && <QueryState error={runMutation.error || probeMutation.error} />
      )}

      <div className="studio-columns">
        <section className="panel">
          <header className="section-header">
            <div><h2>검증 Provider 선택</h2><small>현재 서버에 배포되어 등록된 읽기 전용 크롤러입니다.</small></div>
            {selectedValidationProvider && <StatusBadge status={selectedValidationProvider.status} />}
          </header>
          <QueryState
            loading={providers.isLoading}
            error={providers.error}
            unavailable={providers.data?.available === false || providers.data?.registry_available === false}
            empty={providers.data?.available === true && providers.data.items.length === 0}
          />
          {providers.data?.available === true && providers.data.items.length > 0 ? (
            <div className="stack-form">
              <label>
                Provider
                <select value={validationProvider} onChange={(event) => setValidationProvider(event.target.value)}>
                  {requestedValidationProviderUnavailable ? (
                    <option value={requestedProvider} disabled>{requestedProvider} · 등록되지 않음</option>
                  ) : null}
                  {providers.data.items.map((item) => (
                    <option key={item.provider} value={item.provider}>{item.provider} · {item.content_type}</option>
                  ))}
                </select>
              </label>
              {requestedValidationProviderUnavailable ? (
                <div className="state-panel">요청한 Provider와 정확히 일치하는 등록 크롤러가 없습니다.</div>
              ) : null}
              {selectedValidationProvider && !selectedValidationProvider.can_run ? (
                <div className="deploy-blockers">실행 차단: {selectedValidationProvider.run_blocked_reason || '이 Provider는 현재 검증할 수 없습니다.'}</div>
              ) : null}
            </div>
          ) : null}
        </section>

        <section className="panel">
          <header className="section-header"><div><h2>Parser Probe</h2><small>DB 저장 없이 공개 URL의 필드와 selector 근거를 진단합니다.</small></div></header>
          <form className="stack-form" onSubmit={(event) => { event.preventDefault(); if (probeUrl.trim()) probeMutation.mutate(); }}>
            <label>
              점검할 공개 URL
              <input type="url" value={probeUrl} onChange={(event) => setProbeUrl(event.target.value)} required maxLength={4096} placeholder="https://..." />
            </label>
            <label>
              제한 시간(초)
              <input type="number" value={probeTimeout} min={5} max={60} onChange={(event) => setProbeTimeout(Number(event.target.value))} />
            </label>
            <button className="button primary" type="submit" disabled={session.role === 'viewer' || probeMutation.isPending || routingBlocked}>
              {probeMutation.isPending ? '진단 등록 중…' : 'Parser Probe 등록'}
            </button>
          </form>
        </section>
      </div>

      <section className="panel">
        <header className="section-header">
          <div><h2>Dry-run / Review</h2><small>apply를 제외하고 동시성 1, 재시도 0으로 제한합니다.</small></div>
        </header>
        <form className="studio-run-form" onSubmit={submitRun}>
          <label>
            실행 방식
            <select value={runMode} onChange={(event) => setRunMode(event.target.value as RunMode)}>
              <option value="dry_run">dry_run · 저장하지 않고 비교</option>
              <option value="review">review · 검토 대상으로 보류</option>
            </select>
          </label>
          <label className="check-row"><input type="checkbox" checked={compareExisting} onChange={(event) => setCompareExisting(event.target.checked)} />기존 데이터와 비교</label>
          <label className="check-row"><input type="checkbox" checked={saveHtml} onChange={(event) => setSaveHtml(event.target.checked)} />HTML 근거 저장</label>
          <label className="check-row"><input type="checkbox" checked={saveScreenshot} onChange={(event) => setSaveScreenshot(event.target.checked)} />스크린샷 근거 저장</label>
          <button className="button primary" type="submit" disabled={session.role === 'viewer' || !selectedValidationProvider?.can_run || runMutation.isPending || routingBlocked}>
            {runMutation.isPending ? '검증 등록 중…' : `${runMode} 등록`}
          </button>
        </form>
      </section>

      <section className="panel">
        <header className="section-header">
          <div><h2>최근 검증 결과</h2><small>{validationProvider || '선택한 Provider'}의 실행 결과에서 로그·수집 데이터·품질로 이동합니다.</small></div>
          <Link className="button subtle" to="/data-quality">품질 화면 열기</Link>
        </header>
        <QueryState loading={runs.isLoading} error={runs.error} unavailable={runs.data?.available === false} empty={runs.data?.available !== false && selectedRuns.length === 0} />
        {runs.data?.available !== false && selectedRuns.length > 0 ? (
          <DataTable data={selectedRuns} columns={runColumns} exportName="mooncen-crawler-studio-runs.csv" onRowClick={(run) => navigate(`/crawler-studio/runs/${run.id}`)} />
        ) : null}
      </section>

      {id && (
        <DetailPanel title="Studio 검증 결과" onClose={() => navigate('/crawler-studio')}>
          <QueryState loading={detail.isLoading} error={detail.error} />
          {detail.data ? (
            <>
              <div className="button-row studio-result-links">
                {runJobId && <Link className="button subtle" to={`/jobs/${runJobId}`}>Job 상세</Link>}
                <Link className="button subtle" to={`/content?${new URLSearchParams({ provider: String(detail.data.provider || validationProvider), state: 'active' })}`}>수집 데이터</Link>
                <Link className="button subtle" to="/data-quality">품질 이슈</Link>
              </div>
              <DefinitionList value={detail.data} />
            </>
          ) : null}
          <h3>작업 로그 · 최근 1,000건</h3>
          <QueryState loading={logs.isLoading} error={logs.error} unavailable={logs.data?.available === false} empty={Boolean(runJobId) && logs.data?.items.length === 0} />
          {logs.data?.items.length ? (
            <div className="log-viewer">
              {logs.data.items.map((log) => (
                <div key={String(log.id)}><time>{formatDate(log.created_at)}</time><strong>{String(log.log_level || 'info')}</strong><span>{String(log.message || '')}</span></div>
              ))}
            </div>
          ) : null}
          <h3>실패 근거</h3>
          <QueryState loading={errors.isLoading} error={errors.error} unavailable={errors.data?.available === false} empty={errors.data?.items.length === 0} />
          {errors.data?.items.map((item) => (
            <article className="error-card" key={String(item.id)}><StatusBadge status="failed" /> <strong>{String(item.error_type || 'unknown_error')}</strong><p>{String(item.message || '-')}</p></article>
          ))}
        </DetailPanel>
      )}
    </>
  );
}
