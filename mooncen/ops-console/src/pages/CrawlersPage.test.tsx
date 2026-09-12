import '@testing-library/jest-dom/vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { opsApi } from '../api';
import { OpsProvider } from '../context';
import CrawlersPage from './CrawlersPage';

vi.mock('../api', () => ({
  opsApi: vi.fn(),
}));

const mockedOpsApi = vi.mocked(opsApi);

function renderPage(initialEntry = '/crawlers') {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <OpsProvider
        session={{
          user: { id: 'operator-id', email: 'operator@example.test', name: '운영자' },
          role: 'operator',
          environment: 'development',
        }}
      >
        <MemoryRouter initialEntries={[initialEntry]}>
          <Routes>
            <Route path="/crawlers" element={<CrawlersPage />} />
            <Route path="/crawlers/runs/:id" element={<CrawlersPage />} />
          </Routes>
        </MemoryRouter>
      </OpsProvider>
    </QueryClientProvider>,
  );
}

describe('CrawlersPage automatic runs', () => {
  afterEach(cleanup);

  beforeEach(() => {
    mockedOpsApi.mockReset();
    mockedOpsApi.mockImplementation(async (path: string) => {
      if (path === '/crawlers') {
        return {
          available: true,
          total: 2,
          items: [
            {
              provider: 'HOMEPLUS',
              crawler_name: 'HOMEPLUS',
              content_type: 'culture_center',
              status: 'idle',
              last_run_status: 'success',
              last_run_trigger: 'local_schedule',
              can_run: true,
            },
            {
              provider: 'MUNI_OTHER',
              crawler_name: 'MUNI_OTHER',
              content_type: 'education',
              status: 'idle',
              last_run_status: 'success',
              last_run_trigger: 'manual',
              can_run: true,
            },
          ],
        };
      }
      if (path === '/crawlers/owner/status') {
        return {
          available: true,
          owner: 'gen1crawler',
          host: 'gen1crawler',
          timer: {
            ActiveState: 'active',
            UnitFileState: 'enabled',
            NextElapseUSecRealtime: 'Fri 2026-09-11 22:00:00 KST',
          },
          run: {
            ActiveState: 'inactive',
            SubState: 'dead',
            Result: 'success',
            ExecMainStatus: '0',
            ExecMainStartTimestamp: 'Fri 2026-09-11 10:00:00 KST',
            ExecMainExitTimestamp: 'Fri 2026-09-11 13:00:00 KST',
          },
          dispatch: { running: false, started_at: null, finished_at: null, exit_code: null, error: null },
        };
      }
      if (path === '/crawlers/owner/run-all') {
        return { accepted: true, owner: 'gen1crawler', dispatch: { started_at: '2026-09-11T07:00:00Z' } };
      }
      if (path === '/crawlers/runs?limit=100') {
        return {
          available: true,
          total: 1,
          limit: 100,
          offset: 0,
          items: [
            {
              id: '11111111-1111-4111-8111-111111111111',
              crawler_name: 'HOMEPLUS',
              provider: 'HOMEPLUS',
              content_type: 'culture_center',
              status: 'success',
              run_mode: 'apply',
              total_count: 7859,
              success_count: 7859,
              failed_count: 0,
              new_count: 0,
              updated_count: 7859,
              trigger: 'local_schedule',
              job_id: '22222222-2222-4222-8222-222222222222',
              started_at: '2026-07-27T04:27:29+09:00',
            },
          ],
        };
      }
      if (path === '/crawlers/runs?limit=100&provider=HOMEPLUS') {
        return {
          available: true,
          total: 1,
          limit: 100,
          offset: 0,
          items: [
            {
              id: '11111111-1111-4111-8111-111111111111',
              crawler_name: 'HOMEPLUS',
              provider: 'HOMEPLUS',
              content_type: 'culture_center',
              status: 'success',
              run_mode: 'apply',
              total_count: 7859,
              success_count: 7859,
              failed_count: 0,
              new_count: 0,
              updated_count: 7859,
              trigger: 'local_schedule',
              job_id: '22222222-2222-4222-8222-222222222222',
              started_at: '2026-07-27T04:27:29+09:00',
            },
          ],
        };
      }
      if (path === '/crawlers/runs/11111111-1111-4111-8111-111111111111') {
        return {
          id: '11111111-1111-4111-8111-111111111111',
          provider: 'HOMEPLUS',
          status: 'success',
          trigger: 'local_schedule',
          job_id: '22222222-2222-4222-8222-222222222222',
        };
      }
      if (path === '/crawlers/runs/11111111-1111-4111-8111-111111111111/errors') {
        return { available: true, items: [] };
      }
      if (path === '/jobs/22222222-2222-4222-8222-222222222222/logs?limit=1000&tail=true') {
        return {
          available: true,
          items: [
            {
              id: 1,
              log_level: 'info',
              message: 'Scheduled crawler job completed.',
              created_at: '2026-07-27T04:31:34+09:00',
            },
          ],
        };
      }
      throw new Error(`Unexpected API path: ${path}`);
    });
  });

  it('labels scheduled runs and shows their job logs in the run detail', async () => {
    renderPage();

    expect((await screen.findAllByText('자동')).length).toBeGreaterThanOrEqual(2);
    const runProvider = (await screen.findAllByText('HOMEPLUS'))[1];
    fireEvent.click(runProvider.closest('tr') as HTMLElement);

    expect(await screen.findByText('작업 로그 · 최근 1,000건')).toBeInTheDocument();
    expect(await screen.findByText('Scheduled crawler job completed.')).toBeInTheDocument();
  });

  it('honors an exact provider deep link instead of showing unrelated crawlers', async () => {
    renderPage('/crawlers?provider=HOMEPLUS');

    expect(await screen.findByRole('heading', { name: 'HOMEPLUS 크롤러' })).toBeInTheDocument();
    expect(await screen.findAllByText('HOMEPLUS')).not.toHaveLength(0);
    expect(screen.queryByText('MUNI_OTHER')).not.toBeInTheDocument();
    expect(mockedOpsApi).toHaveBeenCalledWith('/crawlers/runs?limit=100&provider=HOMEPLUS');
    expect(screen.getByRole('link', { name: '개선 큐' })).toHaveAttribute('href', '/crawler-improvements');
  });

  it('requires the production phrase before dispatching a full owner run', async () => {
    const prompt = vi.spyOn(window, 'prompt').mockReturnValue('MOONCEN-CRAWLER-ALL');
    renderPage();

    const button = await screen.findByRole('button', { name: '전체 크롤러 실행' });
    await vi.waitFor(() => expect(button).not.toBeDisabled());
    fireEvent.click(button);

    await vi.waitFor(() => {
      expect(mockedOpsApi).toHaveBeenCalledWith('/crawlers/owner/run-all', {
        method: 'POST',
        body: JSON.stringify({ confirmation: 'MOONCEN-CRAWLER-ALL' }),
      });
    });
    expect(prompt).toHaveBeenCalledWith(expect.stringContaining('MOONCEN-CRAWLER-ALL'));
    expect(await screen.findByText(/gen1crawler가 전체 실행 요청을 접수했습니다/)).toBeInTheDocument();
  });

  it('shows the remote timer and one-shot execution evidence', async () => {
    renderPage();

    expect(await screen.findByText('Fri 2026-09-11 22:00:00 KST')).toBeInTheDocument();
    expect(screen.getByText('Fri 2026-09-11 10:00:00 KST')).toBeInTheDocument();
    expect(screen.getByText('Fri 2026-09-11 13:00:00 KST')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '상태 새로고침' })).toBeInTheDocument();
  });
});
