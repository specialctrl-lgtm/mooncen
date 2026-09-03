import '@testing-library/jest-dom/vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { opsApi } from '../api';
import { OpsProvider } from '../context';
import DeploymentsPage from './DeploymentsPage';

vi.mock('../api', () => ({
  opsApi: vi.fn(),
  opsStreamUrl: vi.fn((path: string) => path),
}));

const mockedOpsApi = vi.mocked(opsApi);

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <OpsProvider session={{
        user: { id: 'admin-id', email: 'admin@example.test', name: '관리자' },
        role: 'admin',
        environment: 'development',
      }}>
        <MemoryRouter initialEntries={['/deployments']}>
          <Routes><Route path="/deployments" element={<DeploymentsPage />} /></Routes>
        </MemoryRouter>
      </OpsProvider>
    </QueryClientProvider>,
  );
}

describe('DeploymentsPage native deployment', () => {
  afterEach(cleanup);

  beforeEach(() => {
    mockedOpsApi.mockImplementation(async (path: string) => {
      if (path === '/services?environment=production') {
        return { available: true, total: 0, limit: 100, offset: 0, items: [] };
      }
      if (path === '/deployments?limit=100') {
        return { available: true, total: 0, limit: 100, offset: 0, items: [] };
      }
      if (path === '/deployments/readiness') {
        return {
          available: true,
          can_deploy: true,
          default_target: 'cloud',
          targets: [{
            name: 'cloud', server: 'cloud', domain: 'mooncen.kr', remote_dir: '/opt/mooncen',
            role: 'primary', deploy_profile: 'full-stack', active: true, key_ready: true,
            services: [{ service: 'frontend', role: 'primary' }, { service: 'backend', role: 'primary' }],
          }],
          snapshot: {
            branch: 'main', commit: '1'.repeat(40), short_commit: '1'.repeat(12), clean: true,
            changed_count: 0, source_tree: '2'.repeat(40), short_source_tree: '2'.repeat(12),
            deploy_path_count: 1400, excluded_count: 20,
          },
          agent: { hostname: 'an2p' },
          reasons: [],
        };
      }
      throw new Error(`unexpected request: ${path}`);
    });
  });

  it('shows only the native deployment workflow', async () => {
    renderPage();
    expect(await screen.findByText('네이티브 배포 스냅샷')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '네이티브 배포' })).toBeDisabled();
    expect(screen.queryByText('Docker 배포 파이프라인')).not.toBeInTheDocument();
  });

  it('does not request retired container endpoints', async () => {
    renderPage();
    await screen.findByText('mooncen.kr');
    expect(mockedOpsApi).not.toHaveBeenCalledWith(
      expect.stringContaining('/deployments/container'),
      expect.anything(),
    );
  });
});
