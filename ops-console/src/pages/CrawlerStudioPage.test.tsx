import '@testing-library/jest-dom/vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { OpsApiError, opsApi } from '../api';
import { OpsProvider } from '../context';
import type { OpsSession } from '../types';
import CrawlerStudioPage from './CrawlerStudioPage';

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>();
  return { ...actual, opsApi: vi.fn() };
});

const mockedOpsApi = vi.mocked(opsApi);
const DRAFT_ID = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
const REVISION_ID = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';
const SOURCE = "print('문센')\n";
const SOURCE_SHA256 = 'd875ae33b43363b97662709b5410d3373d9a262515cea33a2ba9f7b6fe1e902c';
const UPDATED_SOURCE = "print('문센 v2')\n";
const UPDATED_SHA256 = '3344c717297a8f18dbac3d8c6ec5c1b4939900d1b702242db410c06a22b6d9c7';

let apiRole: OpsSession['role'];
let studioDraftItems: Array<Record<string, unknown>>;
let studioDetail: Record<string, unknown>;

afterEach(cleanup);

function capabilities(role: OpsSession['role']) {
  return {
    available: true,
    environment: 'development',
    role,
    capabilities: {
      draft_storage: { available: true, reason: null },
      revision_history: { available: true, reason: null },
      review_decision: { available: true, reason: null },
      source_approval: { available: false, reason: 'independent_source_approval_evidence_not_implemented' },
      fixture_validation: { available: false, reason: 'immutable_fixture_validation_runner_not_implemented' },
      source_execution: { available: false, reason: 'central_sandboxed_source_runner_not_implemented' },
      build: { available: false, reason: 'immutable_builder_evidence_handoff_not_implemented' },
      sign: { available: false, reason: 'signer_is_outside_ops_api' },
      independent_release_approval: { available: false, reason: 'independent_operator_approval_evidence_not_implemented' },
    },
  };
}

function draft(status = 'draft', latestRevision = 1) {
  return {
    id: DRAFT_ID,
    environment: 'development',
    provider: 'HOMEPLUS',
    source_path: 'Crawler/Crawler_Homeplus.py',
    title: 'Homeplus crawler draft',
    status,
    latest_revision: latestRevision,
    impacted_providers: ['HOMEPLUS'],
    created_by: 'operator',
    created_at: '2026-08-12T00:00:00Z',
    updated_at: '2026-08-12T00:00:00Z',
  };
}

function revision(source = SOURCE, sha256 = SOURCE_SHA256, revisionNumber = 1) {
  return {
    id: REVISION_ID,
    draft_id: DRAFT_ID,
    environment: 'development',
    revision: revisionNumber,
    source_sha256: sha256,
    source_size_bytes: new TextEncoder().encode(source).byteLength,
    impacted_providers: ['HOMEPLUS'],
    source_text: source,
    change_summary: 'initial reviewed source',
    created_by: 'operator',
    created_at: '2026-08-12T00:00:00Z',
  };
}

function detail(status = 'draft', latestRevision = 1, source = SOURCE, sha256 = SOURCE_SHA256) {
  return {
    ...draft(status, latestRevision),
    latest_revision_item: revision(source, sha256, latestRevision),
    reviews: [],
  };
}

async function defaultApi(path: string, init?: RequestInit): Promise<unknown> {
  if (path === '/crawler-studio/capabilities') return capabilities(apiRole);
  if (path === '/crawler-studio/providers') {
    return {
      available: true,
      total: 1,
      items: [{ provider: 'HOMEPLUS', source_path: 'Crawler/Crawler_Homeplus.py', impacted_providers: ['HOMEPLUS'] }],
    };
  }
  if (path === '/crawler-studio/drafts?limit=100&offset=0') {
    return { available: true, total: studioDraftItems.length, limit: 100, offset: 0, items: studioDraftItems };
  }
  if (path === `/crawler-studio/drafts/${DRAFT_ID}`) {
    return { available: true, item: studioDetail, capabilities: capabilities(apiRole).capabilities };
  }
  if (path === `/crawler-studio/drafts/${DRAFT_ID}/revisions?limit=100&offset=0`) {
    const item = (studioDetail.latest_revision_item || revision()) as Record<string, unknown>;
    return { available: true, total: 1, limit: 100, offset: 0, items: [item] };
  }
  if (path === '/crawler-studio/drafts' && init?.method === 'POST') {
    const body = JSON.parse(String(init.body));
    studioDetail = {
      ...detail('draft', 1, body.source_text, body.source_sha256),
      title: body.title,
      provider: body.provider,
      source_path: body.source_path,
    };
    studioDraftItems = [studioDetail];
    return { available: true, replayed: false, item: studioDetail };
  }
  if (path === `/crawler-studio/drafts/${DRAFT_ID}/revisions` && init?.method === 'POST') {
    const body = JSON.parse(String(init.body));
    studioDetail = {
      ...studioDetail,
      status: 'draft',
      latest_revision: body.expected_revision + 1,
      latest_revision_item: revision(body.source_text, body.source_sha256, body.expected_revision + 1),
    };
    studioDraftItems = [studioDetail];
    return { available: true, item: studioDetail };
  }
  if (path === `/crawler-studio/drafts/${DRAFT_ID}/reviews` && init?.method === 'POST') {
    const body = JSON.parse(String(init.body));
    const status = { submit: 'in_review', approve: 'approved', request_changes: 'changes_requested', archive: 'archived' }[body.decision as string];
    studioDetail = { ...studioDetail, status };
    studioDraftItems = [studioDetail];
    return { available: true, item: studioDetail };
  }
  if (path === '/crawlers') {
    return {
      available: true,
      registry_available: true,
      items: [
        { provider: 'HOMEPLUS', crawler_name: 'HOMEPLUS', content_type: 'culture_center', status: 'idle', can_run: true },
        { provider: 'MUNI_OTHER', crawler_name: 'MUNI_OTHER', content_type: 'education', status: 'idle', can_run: true },
      ],
    };
  }
  if (path.startsWith('/crawlers/runs?limit=100')) {
    return { available: true, total: 0, limit: 100, offset: 0, items: [] };
  }
  if (path === '/crawlers/run' && init?.method === 'POST') {
    return { job: { id: '22222222-2222-4222-8222-222222222222' }, crawler_run: { id: '11111111-1111-4111-8111-111111111111' } };
  }
  if (path === '/crawlers/runs/11111111-1111-4111-8111-111111111111') {
    return { id: '11111111-1111-4111-8111-111111111111', provider: 'HOMEPLUS', status: 'queued' };
  }
  if (path.endsWith('/errors')) return { available: true, items: [] };
  throw new Error(`Unexpected API path: ${path}`);
}

function renderPage(
  environment: OpsSession['environment'] = 'development',
  role: OpsSession['role'] = 'operator',
  initialEntry = '/crawler-studio',
) {
  apiRole = role;
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <OpsProvider session={{ user: { id: role, email: `${role}@example.test`, name: role }, role, environment }}>
        <MemoryRouter initialEntries={[initialEntry]}>
          <Routes>
            <Route path="/crawler-studio" element={<CrawlerStudioPage />} />
            <Route path="/crawler-studio/runs/:id" element={<CrawlerStudioPage />} />
            <Route path="/jobs/:id" element={<div>job detail</div>} />
          </Routes>
        </MemoryRouter>
      </OpsProvider>
    </QueryClientProvider>,
  );
}

describe('CrawlerStudioPage', () => {
  beforeEach(() => {
    apiRole = 'operator';
    studioDraftItems = [];
    studioDetail = detail();
    mockedOpsApi.mockReset();
    mockedOpsApi.mockImplementation(defaultApi);
  });

  it('creates only an allowlisted draft with the exact client UTF-8 SHA-256', async () => {
    renderPage();

    expect(await screen.findByRole('option', { name: 'Crawler/Crawler_Homeplus.py' })).toBeInTheDocument();
    expect(screen.getByLabelText('검토된 소스 경로').tagName).toBe('SELECT');
    expect(screen.queryByLabelText(/command|명령/i)).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('초안 제목'), { target: { value: 'Homeplus source review' } });
    fireEvent.change(screen.getByLabelText('소스 편집기'), { target: { value: SOURCE } });
    fireEvent.change(screen.getAllByLabelText('변경 요약')[0], { target: { value: 'reviewed initial source' } });
    expect(await screen.findByText(SOURCE_SHA256)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '중앙 초안 저장' }));

    await waitFor(() => {
      const call = mockedOpsApi.mock.calls.find(([path, init]) => path === '/crawler-studio/drafts' && init?.method === 'POST');
      expect(call).toBeDefined();
      const payload = JSON.parse(String(call?.[1]?.body));
      expect(payload).toEqual({
        provider: 'HOMEPLUS',
        source_path: 'Crawler/Crawler_Homeplus.py',
        title: 'Homeplus source review',
        source_text: SOURCE,
        source_sha256: SOURCE_SHA256,
        change_summary: 'reviewed initial source',
      });
      expect(payload).not.toHaveProperty('command');
      expect(payload).not.toHaveProperty('environment');
    });
  });

  it('preselects only an exact Provider supplied by a safe deep link', async () => {
    renderPage('development', 'operator', '/crawler-studio?provider=MUNI_OTHER');

    expect(await screen.findByLabelText('Provider')).toHaveValue('MUNI_OTHER');
    expect(mockedOpsApi).toHaveBeenCalledWith('/crawlers/runs?limit=100&provider=MUNI_OTHER');
    expect(screen.getByRole('link', { name: '개선 큐' })).toHaveAttribute('href', '/crawler-improvements');
  });

  it('preserves an unreviewed deep-link target and blocks draft creation and validation', async () => {
    renderPage('development', 'operator', '/crawler-studio?provider=UNREVIEWED');

    const draftProvider = await screen.findByLabelText('검토된 Provider');
    expect(draftProvider).toHaveValue('UNREVIEWED');
    expect(draftProvider).toBeDisabled();
    expect(screen.getByRole('option', { name: 'UNREVIEWED · Studio 검토 목록 없음' })).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent('다른 Provider로 자동 전환하지 않았으며');
    expect(screen.getByRole('button', { name: '중앙 초안 저장' })).toBeDisabled();

    expect(screen.getByLabelText('Provider')).toHaveValue('UNREVIEWED');
    expect(mockedOpsApi).toHaveBeenCalledWith('/crawlers/runs?limit=100&provider=UNREVIEWED');
    expect(screen.getByRole('option', { name: 'UNREVIEWED · 등록되지 않음' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'dry_run 등록' })).toBeDisabled();
    expect(mockedOpsApi.mock.calls.find(([path, init]) => path === '/crawler-studio/drafts' && init?.method === 'POST')).toBeUndefined();
    expect(mockedOpsApi.mock.calls.find(([path, init]) => path === '/crawlers/run' && init?.method === 'POST')).toBeUndefined();
  });

  it('appends a revision with the current optimistic expected_revision and exact digest', async () => {
    studioDraftItems = [draft('changes_requested', 1)];
    studioDetail = detail('changes_requested', 1);
    renderPage();

    expect(await screen.findByRole('heading', { name: '리비전 편집기' })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('소스', { selector: '.studio-source-editor' }), { target: { value: UPDATED_SOURCE } });
    const summaryFields = screen.getAllByLabelText('변경 요약');
    fireEvent.change(summaryFields[summaryFields.length - 1], { target: { value: 'apply requested selector changes' } });
    expect(await screen.findByText(UPDATED_SHA256)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'r2 리비전 추가' }));

    await waitFor(() => {
      const call = mockedOpsApi.mock.calls.find(([path, init]) => path === `/crawler-studio/drafts/${DRAFT_ID}/revisions` && init?.method === 'POST');
      expect(JSON.parse(String(call?.[1]?.body))).toEqual({
        expected_revision: 1,
        source_text: UPDATED_SOURCE,
        source_sha256: UPDATED_SHA256,
        change_summary: 'apply requested selector changes',
      });
    });
  });

  it('keeps source approval closed until independent evidence exists', async () => {
    studioDraftItems = [draft('in_review', 1)];
    studioDetail = detail('in_review', 1);
    renderPage('development', 'admin');

    const approveButton = await screen.findByRole('button', { name: '소스 승인 · 독립 근거 필요' });
    expect(approveButton).toBeDisabled();
    expect(approveButton).toHaveAttribute('title', 'independent_source_approval_evidence_not_implemented');
    expect(screen.getByText(/배포를 승인하지 않습니다/)).toBeInTheDocument();
    fireEvent.change(await screen.findByLabelText('리뷰 의견'), { target: { value: 'admin reviewed current source' } });
    fireEvent.click(approveButton);
    expect(mockedOpsApi.mock.calls.find(([path, init]) => path === `/crawler-studio/drafts/${DRAFT_ID}/reviews` && init?.method === 'POST')).toBeUndefined();
  });

  it.each([
    { status: 'draft', decision: 'submit', button: '리뷰 제출' },
    { status: 'in_review', decision: 'request_changes', button: '변경 요청' },
    { status: 'draft', decision: 'archive', button: '초안 보관' },
  ])('sends the bounded $decision source-review transition', async ({ status, decision, button }) => {
    studioDraftItems = [draft(status, 1)];
    studioDetail = detail(status, 1);
    renderPage('development', 'operator');

    fireEvent.change(await screen.findByLabelText('리뷰 의견'), { target: { value: `${decision} reviewed comment` } });
    const transitionButton = screen.getByRole('button', { name: button });
    await waitFor(() => expect(transitionButton).toBeEnabled());
    fireEvent.click(transitionButton);
    await waitFor(() => {
      const call = mockedOpsApi.mock.calls.find(([path, init]) => path === `/crawler-studio/drafts/${DRAFT_ID}/reviews` && init?.method === 'POST');
      expect(JSON.parse(String(call?.[1]?.body))).toEqual({
        expected_revision: 1,
        expected_source_sha256: SOURCE_SHA256,
        decision,
        comment: `${decision} reviewed comment`,
      });
    });
  });

  it('blocks every central source mutation when the API environment differs', async () => {
    renderPage('staging', 'operator');

    expect(await screen.findByRole('alert')).toHaveTextContent('Studio 환경 또는 역할 계약');
    fireEvent.change(screen.getByLabelText('초안 제목'), { target: { value: 'Wrong environment draft' } });
    fireEvent.change(screen.getByLabelText('소스 편집기'), { target: { value: SOURCE } });
    fireEvent.change(screen.getAllByLabelText('변경 요약')[0], { target: { value: 'must remain blocked' } });
    expect(await screen.findByText(SOURCE_SHA256)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '중앙 초안 저장' })).toBeDisabled();
  });

  it('blocks review when the editor differs from the stored revision', async () => {
    studioDraftItems = [draft('draft', 1)];
    studioDetail = detail('draft', 1);
    renderPage('development', 'operator');

    fireEvent.change(await screen.findByLabelText('소스', { selector: '.studio-source-editor' }), { target: { value: UPDATED_SOURCE } });
    fireEvent.change(screen.getByLabelText('리뷰 의견'), { target: { value: 'do not review unsaved source' } });
    expect(await screen.findByText(/편집기의 소스가 저장된 최신 리비전과 다릅니다/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '리뷰 제출' })).toBeDisabled();
  });

  it.each(['approved', 'archived'])('reopens an %s source through a new immutable revision', async (status) => {
    studioDraftItems = [draft(status, 1)];
    studioDetail = detail(status, 1);
    renderPage('development', 'operator');

    fireEvent.change(await screen.findByLabelText('소스', { selector: '.studio-source-editor' }), { target: { value: UPDATED_SOURCE } });
    const summaries = screen.getAllByLabelText('변경 요약');
    fireEvent.change(summaries[summaries.length - 1], { target: { value: 'start the next review cycle' } });
    expect(await screen.findByText(UPDATED_SHA256)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'r2 리비전 추가' })).toBeEnabled();
  });

  it('renders unavailable central storage explicitly instead of treating it as zero drafts', async () => {
    mockedOpsApi.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === '/crawler-studio/capabilities') {
        const response = capabilities('operator');
        return { ...response, available: false };
      }
      if (path === '/crawler-studio/providers') return { available: false, total: 0, items: [] };
      if (path === '/crawler-studio/drafts?limit=100&offset=0') return { available: false, total: 0, limit: 100, offset: 0, items: [] };
      return defaultApi(path, init);
    });
    renderPage();

    expect((await screen.findAllByText(/연동되지 않았습니다/)).length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText('중앙 저장소 상태 확인 필요')).toBeInTheDocument();
    expect(screen.queryByText('실제 0건')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '중앙 초안 저장' })).not.toBeInTheDocument();
  });

  it('shows execution, build, signing, and independent release approval as unavailable capabilities', async () => {
    renderPage();

    expect(await screen.findByText('소스 실행')).toBeInTheDocument();
    for (const label of ['독립 소스 승인', '소스 실행', '빌드', '서명', '독립 릴리스 승인']) {
      const card = screen.getByText(label).closest('article');
      expect(card).toHaveTextContent('비활성');
    }
    expect(screen.getByText('central_sandboxed_source_runner_not_implemented')).toBeInTheDocument();
    expect(screen.getByText('signer_is_outside_ops_api')).toBeInTheDocument();
  });

  it('preserves the bounded legacy dry-run workflow for a registered provider', async () => {
    renderPage();

    expect(await screen.findByRole('option', { name: /HOMEPLUS · culture_center/ })).toBeInTheDocument();
    expect(screen.queryByRole('option', { name: /apply/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'dry_run 등록' }));

    await waitFor(() => {
      const call = mockedOpsApi.mock.calls.find(([path, init]) => path === '/crawlers/run' && init?.method === 'POST');
      expect(call).toBeDefined();
      const payload = JSON.parse(String(call?.[1]?.body));
      expect(payload).toMatchObject({ provider: 'HOMEPLUS', run_mode: 'dry_run', concurrency: 1, max_retries: 0 });
      expect(payload.run_mode).not.toBe('apply');
    });
  });

  it('shows an explicit central-routing block for a distributed parser probe 503', async () => {
    mockedOpsApi.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === '/crawlers/parser-probe' && init?.method === 'POST') {
        throw new OpsApiError(503, 'distributed parser probe unavailable');
      }
      return defaultApi(path, init);
    });
    renderPage('staging');

    fireEvent.change(await screen.findByLabelText('점검할 공개 URL'), { target: { value: 'https://example.test/courses' } });
    fireEvent.click(screen.getByRole('button', { name: 'Parser Probe 등록' }));

    expect(await screen.findByText('중앙 작업 라우팅 준비 중')).toBeInTheDocument();
    expect(screen.getByText(/로컬 크롤러를 직접 실행하지 않습니다/)).toBeInTheDocument();
  });
});
