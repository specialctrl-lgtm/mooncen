import '@testing-library/jest-dom/vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { OpsApiError, opsApi } from './api';
import App from './App';

vi.mock('./api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./api')>();
  return { ...actual, opsApi: vi.fn() };
});

const mockedOpsApi = vi.mocked(opsApi);

function renderApp(initialEntries = ['/']) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={initialEntries}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('Ops Console startup session states', () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it('shows the login form only for an unauthenticated 401 response', async () => {
    mockedOpsApi.mockRejectedValue(new OpsApiError(401, 'Missing token'));

    renderApp();

    expect(await screen.findByRole('heading', { name: 'Ops Console 로그인' })).toBeInTheDocument();
    expect(screen.getByLabelText('아이디')).toBeInTheDocument();
  });

  it('shows an infrastructure error and request id for a 5xx session failure', async () => {
    mockedOpsApi.mockRejectedValue(
      new OpsApiError(503, 'Service not ready', { requestId: 'session-request-id' }),
    );

    renderApp();

    expect(await screen.findByRole('heading', { name: 'Ops Console 연결 오류' })).toBeInTheDocument();
    expect(screen.getByText(/session-request-id/)).toBeInTheDocument();
    expect(screen.queryByLabelText('아이디')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '다시 시도' })).toBeInTheDocument();
  });

  it('shows a connection error instead of login for a network failure', async () => {
    mockedOpsApi.mockRejectedValue(
      new OpsApiError(0, 'Ops API에 연결하지 못했습니다.', { kind: 'network' }),
    );

    renderApp();

    expect(await screen.findByRole('heading', { name: 'Ops Console 연결 오류' })).toBeInTheDocument();
    expect(screen.getByText(/Ops API에 연결하지 못했습니다/)).toBeInTheDocument();
  });

  it.each([
    ['/crawler-studio', 'Crawler Studio'],
    ['/crawler-improvements', '크롤러 개선 큐'],
    ['/crawler-releases', 'Crawler Releases'],
    ['/crawler-analytics', 'Crawler Analytics'],
  ])('routes %s to the crawler lifecycle page', async (path, heading) => {
    const unavailableSection = { available: false, complete: false, has_data: null, reasons: [], components: {} };
    mockedOpsApi.mockImplementation(async (apiPath: string) => {
      if (apiPath === '/session') return {
        user: { id: 'operator', email: 'operator@example.test', name: '운영자' },
        role: 'operator',
        environment: 'staging',
      };
      if (apiPath === '/crawlers') return { available: false, registry_available: false, items: [] };
      if (apiPath === '/crawlers/runs?limit=100') return { available: false, total: 0, limit: 100, offset: 0, items: [] };
      if (apiPath === '/crawlers/improvement-queue?limit=500') return {
        schema_version: 1,
        available: false,
        complete: false,
        generated_at: null,
        total: 0,
        limit: 500,
        truncated: false,
        sources: null,
        items: [],
      };
      if (apiPath === '/crawler-control/summary') return { available: false };
      if (apiPath === '/crawler-control/workers') return { available: false, items: [] };
      if (apiPath.startsWith('/crawler-control/')) return { available: false, total: 0, limit: 100, offset: 0, items: [] };
      if (apiPath === '/crawlers/analytics?window_hours=24') return {
        available: false, complete: false, partial: false, environment: 'staging', generated_at: '2026-08-12T00:00:00Z',
        window_hours: 24, heartbeat_timeout_seconds: 120, reasons: [], deployment: unavailableSection,
        collection: unavailableSection, providers: unavailableSection, quality: unavailableSection,
        workers: unavailableSection, queue: unavailableSection,
      };
      throw new Error(`Unexpected API path: ${apiPath}`);
    });

    renderApp([path]);

    expect(await screen.findByRole('heading', { name: heading })).toBeInTheDocument();
  });
});
